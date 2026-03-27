# Functions as Data: AI-Driven Feature Engineering

## Concept

**Current State:**
- Feature *definitions* are data (in `feature_definitions` table)
- Feature *implementations* are code (Python functions in `src/gefion/features/`)

**Proposed State:**
- Feature definitions AND implementations are data
- AI agent can create/modify/test compute functions without code deployment
- Human reviews and promotes successful experiments

## Why This Matters

### 1. Experimentation Velocity

**Current workflow:**
```bash
# Want to try a new feature? Requires code change.
1. Edit src/gefion/features/custom.py
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

AI can learn from successful patterns and use that knowledge to generate better features over time.

**Key Insight**: Store learned patterns as data, just like features and implementations. The AI queries these patterns before generating new code.

#### Schema for Learned Patterns

```sql
-- Store learned patterns about what works
CREATE TABLE feature_patterns (
    id SERIAL PRIMARY KEY,
    pattern_type TEXT NOT NULL,  -- 'window_size', 'weighting_scheme', 'indicator_combo'
    pattern_name TEXT NOT NULL,
    description TEXT,
    context JSONB,  -- When does this pattern apply?
    evidence JSONB,  -- Statistical support
    confidence NUMERIC(5,2),  -- 0-100 score
    first_observed TIMESTAMP DEFAULT NOW(),
    last_validated TIMESTAMP,
    times_validated INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT TRUE,
    UNIQUE (pattern_type, pattern_name)
);

-- Link patterns to successful implementations
CREATE TABLE implementation_patterns (
    implementation_id INTEGER REFERENCES function_implementations(id),
    pattern_id INTEGER REFERENCES feature_patterns(id),
    PRIMARY KEY (implementation_id, pattern_id)
);

-- Track pattern performance over time
CREATE TABLE pattern_performance (
    pattern_id INTEGER REFERENCES feature_patterns(id),
    evaluated_at TIMESTAMP DEFAULT NOW(),
    metric_name TEXT,  -- 'sharpe', 'information_ratio', 'feature_importance'
    metric_value NUMERIC,
    sample_size INTEGER,
    test_symbols TEXT[]
);
```

#### Example Learned Patterns

**Pattern 1: Optimal Window Sizes**
```json
{
  "pattern_type": "window_size",
  "pattern_name": "momentum_7_to_14_optimal",
  "description": "Momentum features perform best with 7-14 day windows",
  "context": {
    "asset_class": "equities",
    "feature_family": "momentum",
    "market_regime": "any"
  },
  "evidence": {
    "tested_windows": [3, 5, 7, 10, 14, 20, 30],
    "best_performing": [7, 10, 14],
    "avg_sharpe": {
      "7": 1.42,
      "10": 1.38,
      "14": 1.35,
      "20": 0.98
    },
    "sample_size": 500
  },
  "confidence": 85.0
}
```

**Pattern 2: Weighting Schemes**
```json
{
  "pattern_type": "weighting_scheme",
  "pattern_name": "exponential_beats_simple",
  "description": "Exponential weighting outperforms simple moving average for momentum",
  "context": {
    "feature_family": "momentum",
    "comparison": "ema vs sma"
  },
  "evidence": {
    "implementations_tested": 47,
    "ema_avg_sharpe": 1.32,
    "sma_avg_sharpe": 1.08,
    "improvement": "22%",
    "p_value": 0.003
  },
  "confidence": 92.0
}
```

**Pattern 3: Feature Combinations**
```json
{
  "pattern_type": "indicator_combo",
  "pattern_name": "rsi_concavity_mean_reversion",
  "description": "RSI + concavity signals mean reversion better than RSI alone",
  "context": {
    "strategy_type": "mean_reversion",
    "required_features": ["rsi", "derivative_rsi_concavity"]
  },
  "evidence": {
    "rsi_alone_sharpe": 0.87,
    "rsi_plus_concavity_sharpe": 1.24,
    "improvement": "43%",
    "win_rate_improvement": "8%"
  },
  "confidence": 78.0
}
```

#### Pattern-Guided Feature Generation

The AI queries learned patterns before generating new implementations:

```python
async def ai_generate_momentum_feature():
    """AI uses learned patterns to create better features."""

    # 1. Query what we've learned about momentum features
    patterns = await mcp.call(
        'get_learned_patterns',
        pattern_type='window_size',
        context_filter={'feature_family': 'momentum'}
    )

    # AI sees: "7-14 day windows work best"
    optimal_windows = patterns[0]['evidence']['best_performing']

    # 2. Query weighting scheme patterns
    weighting_patterns = await mcp.call(
        'get_learned_patterns',
        pattern_type='weighting_scheme'
    )

    # AI sees: "exponential weighting outperforms simple MA"

    # 3. Generate code using learned patterns
    for window in optimal_windows:
        source_code = f"""
def compute_momentum_exp_{window}(source_rows, momentum_specs, return_failures=False):
    '''Exponential momentum with {window}-day window (pattern-guided).'''
    df = pd.DataFrame(source_rows)
    results = []

    for spec in momentum_specs:
        name = spec['name']
        # Use exponential weighting (learned pattern)
        df['pct_change'] = df['value'].pct_change()
        df[name] = df['pct_change'].ewm(span={window}).mean()

        # ... convert to output format

    return results
"""

        # 4. Register and test
        impl_id = await mcp.call(
            'register_feature_implementation',
            function_name=f'momentum_exp_{window}',
            source_code=source_code,
            metadata={{'guided_by_patterns': [patterns[0]['id'], weighting_patterns[0]['id']]}}
        )

        # 5. If successful, validate the pattern (reinforcement)
        test_result = await mcp.call('test_feature_implementation', impl_id, ...)

        if test_result['sharpe'] > 1.5:
            await mcp.call(
                'validate_pattern',
                pattern_id=patterns[0]['id'],
                new_evidence={{'impl_id': impl_id, 'sharpe': test_result['sharpe']}}
            )
```

#### Reinforcement Learning Loop

Each successful experiment strengthens the pattern's confidence score:

```
AI generates feature → tests on data → if successful → pattern confidence ↑
                                    → if fails → pattern confidence ↓

After N validations, pattern becomes "established" (confidence > 90)
AI prioritizes generating features using established patterns
```

This creates a **compounding advantage**: the more the AI experiments, the smarter it gets about what to try next.

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
from gefion.features.derivatives import compute_derivatives
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
        'next_steps': 'Run: Gefion functions-review --id ' + str(impl_id)
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
def get_learned_patterns(
    pattern_type: Optional[str] = None,
    min_confidence: float = 70.0,
    context_filter: Optional[Dict] = None
) -> List[Dict]:
    """
    Retrieve learned patterns to guide feature generation.

    AI agents call this before generating new features to incorporate
    what the system has already learned.

    Args:
        pattern_type: Filter by pattern type ('window_size', 'weighting_scheme', etc.)
        min_confidence: Minimum confidence score (0-100)
        context_filter: JSONB filter for pattern context

    Returns:
        List of patterns with evidence and example implementations
    """
    query = """
        SELECT
            fp.*,
            json_agg(
                json_build_object(
                    'impl_id', fi.id,
                    'function_name', fi.function_name,
                    'version', fi.version,
                    'performance', fi.performance_metrics
                )
            ) as example_implementations
        FROM feature_patterns fp
        LEFT JOIN implementation_patterns ip ON ip.pattern_id = fp.id
        LEFT JOIN function_implementations fi ON fi.id = ip.implementation_id
        WHERE fp.active = true
          AND fp.confidence >= %s
    """

    params = [min_confidence]

    if pattern_type:
        query += " AND fp.pattern_type = %s"
        params.append(pattern_type)

    if context_filter:
        query += " AND fp.context @> %s"
        params.append(json.dumps(context_filter))

    query += " GROUP BY fp.id ORDER BY fp.confidence DESC"

    return db.query(query, params)


@server.tool()
def validate_pattern(
    pattern_id: int,
    new_evidence: Dict
) -> Dict:
    """
    Update pattern based on new experimental results.

    Called after AI tests a feature that uses this pattern.
    Performs Bayesian update of confidence score.

    Args:
        pattern_id: Pattern to validate
        new_evidence: Results from new experiment (e.g., {'impl_id': 123, 'sharpe': 1.42})

    Returns:
        Updated pattern with new confidence score
    """
    # Get current pattern
    pattern = db.query_one(
        "SELECT * FROM feature_patterns WHERE id = %s",
        [pattern_id]
    )

    # Bayesian update of confidence
    prior_confidence = pattern['confidence']
    times_validated = pattern['times_validated']

    # Simple update formula (can be made more sophisticated)
    if new_evidence.get('sharpe', 0) > 1.0:
        # Positive evidence
        new_confidence = min(100, prior_confidence + (100 - prior_confidence) * 0.1)
    else:
        # Negative evidence
        new_confidence = max(0, prior_confidence - prior_confidence * 0.15)

    # Update pattern
    db.execute("""
        UPDATE feature_patterns
        SET confidence = %s,
            last_validated = NOW(),
            times_validated = times_validated + 1
        WHERE id = %s
    """, [new_confidence, pattern_id])

    # Record performance metric
    db.execute("""
        INSERT INTO pattern_performance
        (pattern_id, metric_name, metric_value, sample_size)
        VALUES (%s, 'sharpe', %s, 1)
    """, [pattern_id, new_evidence.get('sharpe')])

    return {
        'pattern_id': pattern_id,
        'old_confidence': prior_confidence,
        'new_confidence': new_confidence,
        'times_validated': times_validated + 1
    }


@server.tool()
def analyze_implementations(
    function_name_pattern: str,
    min_sample_size: int = 10
) -> Dict:
    """
    Analyze existing implementations to discover patterns.

    AI can periodically run this to extract new patterns from
    the accumulated implementation history.

    Args:
        function_name_pattern: SQL LIKE pattern (e.g., 'momentum_%')
        min_sample_size: Minimum implementations needed to establish pattern

    Returns:
        Discovered patterns with statistical evidence
    """
    # Find all matching implementations
    impls = db.query("""
        SELECT
            function_name,
            source_code,
            performance_metrics,
            approved_at
        FROM function_implementations
        WHERE function_name LIKE %s
          AND active = true
          AND approved_at IS NOT NULL
        ORDER BY performance_metrics->>'sharpe' DESC
    """, [function_name_pattern])

    if len(impls) < min_sample_size:
        return {
            'status': 'insufficient_data',
            'found': len(impls),
            'required': min_sample_size
        }

    # Extract parameters from source code
    # (simplified - real implementation would use AST parsing)
    discovered_patterns = []

    # Example: Discover window size patterns
    window_performance = {}
    for impl in impls:
        # Parse window size from code (regex/AST)
        window = extract_window_size(impl['source_code'])
        sharpe = impl['performance_metrics'].get('sharpe', 0)

        if window not in window_performance:
            window_performance[window] = []
        window_performance[window].append(sharpe)

    # Calculate average performance per window
    for window, sharpes in window_performance.items():
        avg_sharpe = np.mean(sharpes)
        if avg_sharpe > 1.2 and len(sharpes) >= 3:
            # Create pattern record
            pattern_id = db.insert("""
                INSERT INTO feature_patterns
                (pattern_type, pattern_name, description, evidence, confidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (pattern_type, pattern_name) DO UPDATE
                SET evidence = EXCLUDED.evidence,
                    confidence = EXCLUDED.confidence
                RETURNING id
            """, [
                'window_size',
                f'{function_name_pattern}_window_{window}_optimal',
                f'Window size {window} performs well for {function_name_pattern} features',
                json.dumps({
                    'window': window,
                    'avg_sharpe': avg_sharpe,
                    'sample_size': len(sharpes)
                }),
                min(95, avg_sharpe * 50)  # Confidence based on performance
            ])

            discovered_patterns.append({
                'pattern_id': pattern_id,
                'type': 'window_size',
                'window': window,
                'avg_sharpe': avg_sharpe
            })

    return {
        'status': 'success',
        'analyzed': len(impls),
        'discovered': discovered_patterns
    }
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
gefion functions-review --status pending

# Review implementation
gefion functions-review --id 123

# Shows:
# - Source code (syntax highlighted)
# - Test results
# - Performance benchmarks
# - Comparison with existing version

# Approve
gefion functions-approve --id 123

# Reject
gefion functions-reject --id 123 --reason "Uses inefficient algorithm"
```

---

## Benefits

### For AI Agents

1. **Rapid Iteration**: Test hundreds of variants without deployment cycle
2. **Meta-Learning**: Access learned patterns to generate better features over time
3. **Autonomous Experimentation**: Create features end-to-end
4. **Feedback Loop**: Immediate results from real data strengthen pattern confidence
5. **Compounding Advantage**: Each experiment makes the AI smarter about what to try next

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
$ Gefion functions-review --id 456

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

$ Gefion functions-approve --id 456
✅ Implementation approved and activated
```

### Step 5: Use Immediately

```bash
# Feature definition can now use this implementation
gefion features-register --definition '{
    "name": "momentum_exp_20",
    "function_name": "exponential_momentum",
    "params": {"span": 20},
    "implementation_id": 456,
    ...
}'

# Compute it
gefion features-compute --features momentum_exp_20
```

---

## Open Questions

1. **Language Support**: Start with Python only, or support SQL/JS too?
2. **Version Control**: Integrate with git or purely DB-driven?
3. **Performance**: JIT compilation for hot functions?
4. **Testing**: How comprehensive should AI-generated tests be?
5. **Rollback**: Automatic rollback if performance degrades?
6. **Pattern Confidence**: What's the right Bayesian update formula for pattern confidence?
7. **Pattern Decay**: Should pattern confidence decay over time if not re-validated?
8. **Cross-Pattern Learning**: How to discover relationships between patterns (e.g., "window_size=7 + ema weighting works well together")?
9. **Pattern Export**: Should patterns be shareable across users/systems as JSON?
10. **Cold Start**: How to bootstrap the system when no patterns exist yet?

---

## Next Steps

### Phase 1: Foundation
1. **Prototype**: Implement basic function_implementations table
2. **Dynamic Loading**: Update dispatcher to load functions from DB
3. **Safety**: Implement code validation and sandboxing

### Phase 2: MCP Server
4. **Basic MCP Tools**: Build register/test/compare implementations
5. **Pattern Storage**: Add feature_patterns tables
6. **Pattern MCP Tools**: Add get_learned_patterns, validate_pattern, analyze_implementations

### Phase 3: AI Agent
7. **Simple Agent**: Create agent that experiments with momentum variants
8. **Pattern-Guided Generation**: Agent queries patterns before generating code
9. **Pattern Discovery**: Periodically analyze implementations to extract patterns

### Phase 4: Human Review & Production
10. **Review CLI**: Build tool for reviewing AI-generated functions
11. **Monitoring**: Track pattern confidence and implementation performance
12. **Deployment**: Integrate with production feature computation pipeline

---

## References

- [ML_ROADMAP.md](ML_ROADMAP.md) - High-level ML goals
- [FEATURE_DISPATCHER.md](FEATURE_DISPATCHER.md) - Current dispatcher architecture
- [SECURITY_SANDBOXING.md](SECURITY_SANDBOXING.md) - Security considerations for dynamic code execution
- MCP Protocol: https://modelcontextprotocol.io/

---

*Last updated: 2024-12-03 - Added comprehensive meta-learning section with pattern storage schema and MCP tools*
