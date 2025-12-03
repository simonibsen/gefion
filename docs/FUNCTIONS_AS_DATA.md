# Functions as Data: AI-Driven Feature Engineering

## Concept

**Current State:**
- Feature *definitions* are data (in `feature_definitions` table)
- Feature *implementations* are code (Python functions in `src/g2/features/`)

**Proposed State:**
- Feature definitions AND implementations are data
- AI agent can create/modify/test compute functions without code deployment
- Human reviews and promotes successful experiments

## Why This Matters

### 1. Experimentation Velocity

**Current workflow:**
```bash
# Want to try a new feature? Requires code change.
1. Edit src/g2/features/custom.py
2. Write compute_custom_feature() function
3. Register function: register_compute_function('custom', compute_custom_feature)
4. pip install -e .  # Reinstall package
5. Test
6. Commit code
7. Deploy
```

**With functions as data:**
```bash
# AI agent can do this in seconds
1. AI generates function code as string
2. Store in function_implementations table
3. Dispatcher eval()s it on demand
4. Test immediately
5. If good → promote to permanent
6. If bad → rollback with one query
```

### 2. A/B Testing at Function Level

```sql
-- Run same feature definition with different implementations
SELECT
    f1.value as implementation_v1,
    f2.value as implementation_v2,
    ABS(f1.value - f2.value) as difference
FROM computed_features f1
JOIN computed_features f2 ON f1.data_id = f2.data_id AND f1.date = f2.date
WHERE f1.feature_id = 'rsi_experimental_v1'
  AND f2.feature_id = 'rsi_experimental_v2';
```

### 3. Meta-Learning

AI can learn from successful patterns:
```python
# Which implementations performed best?
# What parameters work across features?
# Can we generate better versions automatically?
```

---

## Architecture

### Schema

```sql
CREATE TABLE function_implementations (
    id SERIAL PRIMARY KEY,
    function_name TEXT NOT NULL,
    version TEXT NOT NULL,
    language TEXT DEFAULT 'python',  -- Future: 'sql', 'js'
    source_code TEXT NOT NULL,
    signature JSONB,  -- Function signature/interface
    dependencies TEXT[],  -- Required packages
    safety_level TEXT,  -- 'safe', 'review_required', 'sandbox_only'
    created_by TEXT,  -- 'ai_agent', 'human', 'system'
    created_at TIMESTAMP DEFAULT NOW(),
    test_results JSONB,  -- Unit test outcomes
    performance_metrics JSONB,  -- Execution time, memory
    active BOOLEAN DEFAULT FALSE,
    approved_by TEXT,  -- Human who reviewed
    approved_at TIMESTAMP,
    UNIQUE (function_name, version)
);

-- Link feature definitions to implementations
ALTER TABLE feature_definitions
ADD COLUMN implementation_id INTEGER REFERENCES function_implementations(id);
```

### Dispatcher Changes

```python
# Current: Import functions statically
from g2.features.derivatives import compute_derivatives
register_compute_function('derivative', compute_derivatives)

# New: Load functions dynamically
def load_compute_function(function_name: str) -> Callable:
    """Load compute function from database or code."""

    # Check if there's a DB implementation
    impl = db.query("""
        SELECT source_code, dependencies
        FROM function_implementations
        WHERE function_name = %s AND active = true
        ORDER BY created_at DESC LIMIT 1
    """, [function_name])

    if impl:
        # Dynamic implementation from DB
        return compile_function(impl['source_code'], impl['dependencies'])
    else:
        # Fallback to static code imports
        return import_static_function(function_name)

def compile_function(source_code: str, dependencies: List[str]) -> Callable:
    """
    Safely compile and execute function code.

    Security considerations:
    - Run in restricted namespace (no __import__, eval, exec)
    - Limit available builtins
    - Sandbox execution if safety_level = 'sandbox_only'
    """
    # Create restricted globals
    safe_globals = {
        'pd': pd,
        'np': np,
        'List': List,
        'Dict': Dict,
        # ... whitelist safe imports
    }

    # Compile code
    exec(source_code, safe_globals)

    # Extract function (assuming it's named same as function_name)
    return safe_globals[function_name]
```

---

## AI Integration via MCP

### MCP Server Interface

```python
# g2_mcp_server.py
from mcp import MCPServer

server = MCPServer("g2-features")

@server.tool()
def register_feature_implementation(
    function_name: str,
    source_code: str,
    description: str,
    test_cases: List[Dict]
) -> Dict:
    """
    Register a new feature implementation.

    Args:
        function_name: Name of compute function (e.g., 'custom_momentum')
        source_code: Python code implementing the function
        description: Human-readable description
        test_cases: List of test inputs/expected outputs

    Returns:
        {
            'implementation_id': 123,
            'status': 'pending_review',
            'test_results': {...}
        }
    """
    # 1. Validate code (syntax check, no dangerous imports)
    validate_code_safety(source_code)

    # 2. Run unit tests
    test_results = run_tests(source_code, test_cases)

    # 3. Store in DB
    impl_id = db.insert("""
        INSERT INTO function_implementations
        (function_name, version, source_code, created_by, test_results, safety_level)
        VALUES (%s, %s, %s, 'ai_agent', %s, 'review_required')
        RETURNING id
    """, [function_name, generate_version(), source_code, test_results])

    # 4. Return status
    return {
        'implementation_id': impl_id,
        'status': 'pending_review',
        'test_results': test_results,
        'next_steps': 'Run: g2 functions-review --id ' + str(impl_id)
    }

@server.tool()
def test_feature_implementation(
    implementation_id: int,
    symbol: str,
    start_date: str,
    end_date: str
) -> Dict:
    """
    Test an implementation on real data.

    Returns computed features for review.
    """
    # Load implementation
    impl = db.get_implementation(implementation_id)

    # Compile function
    compute_fn = compile_function(impl['source_code'])

    # Fetch test data
    source_data = fetch_source_data(symbol, start_date, end_date)

    # Run computation
    results = compute_fn(source_data, impl['params'])

    # Return for AI to analyze
    return {
        'symbol': symbol,
        'date_range': [start_date, end_date],
        'results': results,
        'stats': {
            'row_count': len(results),
            'null_count': count_nulls(results),
            'mean': np.mean([r['value'] for r in results]),
            'std': np.std([r['value'] for r in results])
        }
    }

@server.tool()
def compare_implementations(
    function_name: str,
    version_a: str,
    version_b: str,
    test_symbols: List[str]
) -> Dict:
    """
    Compare two implementations side-by-side.

    AI can use this to evaluate if new version is better.
    """
    # Run both versions
    results_a = compute_with_version(function_name, version_a, test_symbols)
    results_b = compute_with_version(function_name, version_b, test_symbols)

    # Compare
    correlation = compute_correlation(results_a, results_b)
    performance_a = benchmark_performance(results_a)
    performance_b = benchmark_performance(results_b)

    return {
        'correlation': correlation,
        'performance': {
            'version_a': performance_a,
            'version_b': performance_b
        },
        'differences': compute_differences(results_a, results_b)
    }

@server.tool()
def list_feature_patterns() -> List[Dict]:
    """
    Return successful feature implementation patterns.

    AI can learn from these when creating new features.
    """
    return db.query("""
        SELECT
            fi.function_name,
            fi.source_code,
            fi.performance_metrics,
            fi.created_at,
            COUNT(fd.id) as usage_count
        FROM function_implementations fi
        LEFT JOIN feature_definitions fd ON fd.implementation_id = fi.id
        WHERE fi.active = true
          AND fi.approved_at IS NOT NULL
        GROUP BY fi.id
        ORDER BY usage_count DESC
        LIMIT 20
    """)
```

### AI Agent Workflow

```python
# AI agent using MCP to experiment with features
async def experiment_with_momentum_features():
    """
    AI-driven feature engineering experiment.

    Goal: Find best momentum calculation for 7-day predictions.
    """

    # 1. AI generates candidate implementations
    for variant in ['ema', 'sma', 'linear_regression', 'polynomial']:
        source_code = generate_momentum_code(variant)

        # 2. Register via MCP
        result = await mcp.call(
            'register_feature_implementation',
            function_name=f'momentum_{variant}',
            source_code=source_code,
            description=f'Momentum using {variant} method',
            test_cases=generate_test_cases()
        )

        # 3. Test on sample data
        test_result = await mcp.call(
            'test_feature_implementation',
            implementation_id=result['implementation_id'],
            symbol='AAPL',
            start_date='2024-01-01',
            end_date='2024-12-01'
        )

        # 4. Evaluate results
        if is_good_result(test_result):
            print(f"✅ {variant} looks promising: {test_result['stats']}")
        else:
            print(f"❌ {variant} underperformed")

    # 5. Compare best candidates
    comparison = await mcp.call(
        'compare_implementations',
        function_name='momentum',
        version_a='ema',
        version_b='linear_regression',
        test_symbols=['AAPL', 'MSFT', 'GOOGL']
    )

    # 6. Recommend best version for human review
    print(f"Recommendation: Use {comparison['best']} (correlation: {comparison['correlation']})")
```

---

## Safety & Security

### 1. Code Validation

```python
def validate_code_safety(source_code: str) -> None:
    """
    Check code for dangerous patterns.

    Raises SecurityError if code is unsafe.
    """
    dangerous_patterns = [
        'eval(',
        'exec(',
        '__import__',
        'open(',
        'os.system',
        'subprocess',
        'socket',
    ]

    for pattern in dangerous_patterns:
        if pattern in source_code:
            raise SecurityError(f"Dangerous pattern detected: {pattern}")

    # Parse AST to detect more complex patterns
    tree = ast.parse(source_code)
    visitor = SafetyVisitor()
    visitor.visit(tree)
```

### 2. Sandboxed Execution

```python
def compile_function_sandboxed(source_code: str) -> Callable:
    """
    Execute in restricted sandbox.

    - No file system access
    - No network access
    - Limited CPU time
    - Limited memory
    """
    # Use RestrictedPython or similar
    from RestrictedPython import compile_restricted

    compiled = compile_restricted(source_code, '<string>', 'exec')

    # Limited globals
    safe_globals = {
        'pd': pd,
        'np': np,
        # No os, sys, subprocess, etc.
    }

    # Execute with timeout
    with Timeout(seconds=60):
        exec(compiled, safe_globals)

    return safe_globals[function_name]
```

### 3. Human Review Workflow

```bash
# List pending implementations
g2 functions-review --status pending

# Review implementation
g2 functions-review --id 123

# Shows:
# - Source code (syntax highlighted)
# - Test results
# - Performance benchmarks
# - Comparison with existing version

# Approve
g2 functions-approve --id 123

# Reject
g2 functions-reject --id 123 --reason "Uses inefficient algorithm"
```

---

## Benefits

### For AI Agents

1. **Rapid Iteration**: Test hundreds of variants without deployment cycle
2. **Learning**: Access to historical successful patterns
3. **Autonomous Experimentation**: Create features end-to-end
4. **Feedback Loop**: Immediate results from real data

### For Humans

1. **Review Not Write**: Focus on reviewing AI-generated code, not writing it
2. **Best-of-N**: AI generates N candidates, human picks best
3. **Provenance**: Full audit trail of who (AI/human) created what
4. **Rollback**: Easy revert to previous versions

### For System

1. **Extensibility**: No code deployment needed for new features
2. **A/B Testing**: Compare implementations empirically
3. **Performance Monitoring**: Track execution time per implementation
4. **Hot-Swapping**: Switch implementations without restart

---

## Example: AI Creates New Feature

### Step 1: AI Generates Code

```python
# AI prompt: "Create a momentum feature using exponential weighting"

def compute_exponential_momentum(
    source_rows: List[Dict[str, Any]],
    momentum_specs: List[Dict[str, Any]],
    return_failures: bool = False
) -> List[Dict[str, Any]]:
    """
    Compute exponential weighted momentum.

    More weight to recent prices, exponentially decaying.
    """
    if not source_rows:
        return [] if not return_failures else ([], [])

    df = pd.DataFrame(source_rows)
    results = []
    failures = []

    for spec in momentum_specs:
        name = spec['name']
        span = spec.get('span', 20)  # EMA span

        try:
            # Exponential moving average of price changes
            df['pct_change'] = df['value'].pct_change()
            df[name] = df['pct_change'].ewm(span=span).mean()

            # Convert to output format
            for _, row in df.iterrows():
                if pd.notna(row[name]):
                    results.append({
                        'date': row['date'],
                        name: float(row[name])
                    })

        except Exception as e:
            failures.append((name, str(e)))

    return (results, failures) if return_failures else results
```

### Step 2: AI Registers via MCP

```python
result = await mcp.call(
    'register_feature_implementation',
    function_name='exponential_momentum',
    source_code=code,
    description='Exponential weighted momentum with decay',
    test_cases=[
        {
            'input': [
                {'date': '2024-01-01', 'value': 100},
                {'date': '2024-01-02', 'value': 102},
                {'date': '2024-01-03', 'value': 101},
            ],
            'expected_output': [
                # Expected momentum values
            ]
        }
    ]
)
# Returns: {'implementation_id': 456, 'status': 'pending_review'}
```

### Step 3: AI Tests Implementation

```python
test_result = await mcp.call(
    'test_feature_implementation',
    implementation_id=456,
    symbol='AAPL',
    start_date='2024-01-01',
    end_date='2024-12-01'
)

# AI analyzes results
if test_result['stats']['null_count'] == 0:
    print("✅ No null values - good")
if test_result['stats']['std'] > 0:
    print("✅ Non-constant values - good")
```

### Step 4: Human Reviews

```bash
$ g2 functions-review --id 456

Function: exponential_momentum
Version: 2024-12-03-v1
Created by: ai_agent
Status: pending_review

Source Code:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def compute_exponential_momentum(...):
    """Exponential weighted momentum."""
    # ... (code shown with syntax highlighting)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Test Results: ✅ All tests passed

Performance:
  - Avg execution time: 23ms
  - Memory usage: 12MB
  - Compared to existing: 15% faster

Comparison with current implementation:
  - Correlation: 0.87
  - Differences: Mainly in smoothness (expected due to EMA)

[A]pprove  [R]eject  [T]est more  [C]ompare

$ g2 functions-approve --id 456
✅ Implementation approved and activated
```

### Step 5: Use Immediately

```bash
# Feature definition can now use this implementation
g2 features-register --definition '{
    "name": "momentum_exp_20",
    "function_name": "exponential_momentum",
    "params": {"span": 20},
    "implementation_id": 456,
    ...
}'

# Compute it
g2 features-compute --features momentum_exp_20
```

---

## Open Questions

1. **Language Support**: Start with Python only, or support SQL/JS too?
2. **Version Control**: Integrate with git or purely DB-driven?
3. **Performance**: JIT compilation for hot functions?
4. **Testing**: How comprehensive should AI-generated tests be?
5. **Rollback**: Automatic rollback if performance degrades?

---

## Next Steps

1. **Prototype**: Implement basic function_implementations table
2. **MCP Server**: Build minimal MCP server with register/test tools
3. **Safety**: Implement code validation and sandboxing
4. **AI Agent**: Create simple agent that experiments with momentum variants
5. **Human Review**: Build CLI tool for reviewing AI-generated functions

---

## References

- [ML_ROADMAP.md](ML_ROADMAP.md) - High-level ML goals
- [FEATURE_DISPATCHER.md](FEATURE_DISPATCHER.md) - Current dispatcher architecture
- MCP Protocol: https://modelcontextprotocol.io/
