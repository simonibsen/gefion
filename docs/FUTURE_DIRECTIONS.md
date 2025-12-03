# Future Directions

## Overview

This document outlines potential future enhancements to the g2 system. These are **not currently planned for immediate implementation**, but represent exciting directions the architecture naturally supports.

---

## 1. AI-Driven Feature Engineering (HIGH PRIORITY)

### Vision

Enable AI agents to autonomously experiment with feature implementations, moving from "features as data" to **"functions as data"**.

### Current State

- ✅ Feature *definitions* are data (`feature_definitions` table)
- ✅ Feature *values* are data (`computed_features` table)
- ❌ Feature *implementations* are code (Python functions)

### Proposed State

- ✅ Feature *definitions* are data
- ✅ Feature *values* are data
- 🆕 Feature *implementations* are data (`function_implementations` table)

### Key Benefits

1. **Rapid Experimentation**: AI can test hundreds of feature variants without code deployment
2. **Meta-Learning**: System learns which implementations perform best
3. **Autonomous Optimization**: AI continuously improves feature engineering
4. **Human-in-Loop**: Humans review and approve AI-generated code
5. **A/B Testing**: Compare implementations empirically on historical data

### Architecture Sketch

```sql
-- Store function implementations as data
CREATE TABLE function_implementations (
    id SERIAL PRIMARY KEY,
    function_name TEXT NOT NULL,
    version TEXT NOT NULL,
    source_code TEXT NOT NULL,
    created_by TEXT,  -- 'ai_agent' or 'human'
    approved_by TEXT,
    active BOOLEAN DEFAULT FALSE,
    UNIQUE (function_name, version)
);

-- Link features to implementations
ALTER TABLE feature_definitions
ADD COLUMN implementation_id INTEGER REFERENCES function_implementations(id);
```

### AI Integration via MCP

AI agents would interact via Model Context Protocol (MCP) server:

```python
# AI agent workflow
1. Generate feature implementation code
2. Register via MCP: register_feature_implementation(code)
3. Test on historical data: test_feature_implementation(id)
4. Compare with baseline: compare_implementations(v1, v2)
5. Submit for human review
6. Human approves → activate for production
```

### Example Use Case

```python
# AI generates 100 momentum variants
for decay_rate in [0.01, 0.02, ..., 0.99]:
    code = generate_momentum_code(decay_rate)
    impl_id = mcp.register_implementation(code)
    results = mcp.test_implementation(impl_id, symbols=['AAPL', 'MSFT'])

    if results['sharpe'] > 1.5:
        print(f"✅ decay_rate={decay_rate} shows promise")
        mcp.submit_for_review(impl_id)

# AI finds best performing variant automatically
# Human reviews top 5 candidates and approves best one
```

### Safety & Security

Critical requirements:
- **Code Validation**: AST parsing to detect dangerous patterns
- **Sandboxed Execution**: No file/network/subprocess access
- **Human Review**: All AI code requires approval before production
- **Monitoring**: Track performance, auto-rollback on degradation
- **Provenance**: Full audit trail of who created what

### Implementation Phases

**Phase 1: Foundation**
- [ ] Add `function_implementations` table
- [ ] Implement dynamic function loading in dispatcher
- [ ] Build code validation and sandboxing

**Phase 2: MCP Server**
- [ ] Create MCP server with basic tools
- [ ] Implement register/test/compare workflows
- [ ] Build human review CLI

**Phase 3: AI Agent**
- [ ] Create simple AI agent for momentum experiments
- [ ] Test full workflow end-to-end
- [ ] Measure experimentation velocity

**Phase 4: Production**
- [ ] Add performance monitoring
- [ ] Implement automatic rollback
- [ ] Scale to handle hundreds of experiments

### References

- [FUNCTIONS_AS_DATA.md](FUNCTIONS_AS_DATA.md) - Detailed design document
- [ML_ROADMAP.md](ML_ROADMAP.md) - ML system goals
- MCP Protocol: https://modelcontextprotocol.io/

### Status

**Priority**: HIGH (likely next major feature after ML predictions)
**Feasibility**: High (architecture already supports this)
**Risk**: Medium (security concerns with dynamic code execution)
**Impact**: VERY HIGH (enables autonomous feature engineering)

---

## 2. Real-Time Feature Serving

### Vision

Serve features with < 10ms latency for live trading decisions.

### Approach

- TimescaleDB continuous aggregates for pre-computed features
- Redis cache for hot features (last 1 day)
- Incremental updates on new data arrival

### Status

**Priority**: Medium (only needed for live trading)
**Feasibility**: High (TimescaleDB + Redis well-proven)

---

## 3. Distributed Feature Computation

### Vision

Scale feature computation across multiple machines for large universes (10,000+ stocks).

### Approach

- Celery/Ray for distributed task execution
- Partition stocks across workers
- Aggregate results in central database

### Status

**Priority**: Low (current system handles 500-1000 stocks fine)
**Feasibility**: Medium (requires infrastructure changes)

---

## 4. Feature Marketplace

### Vision

Community-contributed features that others can use.

### Approach

- Public registry of feature implementations
- Rating/review system for quality
- Standardized interfaces for compatibility

### Status

**Priority**: Low (community not yet established)
**Feasibility**: Medium (requires governance model)

---

## 5. Automated Hyperparameter Tuning

### Vision

AI optimizes not just implementations but also hyperparameters (window sizes, thresholds, etc.)

### Approach

- Bayesian optimization over parameter space
- Multi-objective optimization (performance + interpretability)
- Automatic A/B testing of parameter combinations

### Status

**Priority**: Medium (high value, moderate complexity)
**Feasibility**: High (many existing tools: Optuna, Ray Tune)

---

## Decision Framework

When evaluating future directions, consider:

1. **Alignment**: Does it fit the metadata-driven architecture?
2. **Value**: What problem does it solve? For whom?
3. **Cost**: Implementation complexity? Ongoing maintenance?
4. **Risk**: What could go wrong? How to mitigate?
5. **Timing**: Is now the right time, or should we wait?

**Current Recommendation**: Focus on **AI-Driven Feature Engineering** next. It's:
- ✅ High value (accelerates ML research)
- ✅ Natural extension of existing architecture
- ✅ Manageable risk (can start with simple sandbox)
- ✅ Enables rapid experimentation (core project goal)

---

## Contributing

Have ideas for future directions? Please document them here with:
- Vision (what would this enable?)
- Approach (how might we build it?)
- Status (priority, feasibility, impact)
- References (links to relevant docs/research)

---

*Last updated: 2024-12-03*
