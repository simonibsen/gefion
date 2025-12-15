# g2 Documentation

## Quick Start

- **[USER_GUIDE.md](USER_GUIDE.md)** - How to use g2 CLI
- **[ML_QUICKSTART.md](ML_QUICKSTART.md)** - End-to-end ML workflow guide
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Common issues and solutions

## Architecture & Design

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design and DB-first architecture
- **[PERFORMANCE.md](PERFORMANCE.md)** - Optimization techniques and benchmarks

## Archive

Historical documentation, detailed design docs, and ML vision:

### ML Vision & Future Plans
- **[archive/ml/HIGHLEVEL.md](archive/ml/HIGHLEVEL.md)** - Long-term ML-driven analysis goals
- **[archive/ml/ML_ROADMAP.md](archive/ml/ML_ROADMAP.md)** - Detailed ML implementation plan
- **[archive/ml/ML_SYSTEM_DESIGN.md](archive/ml/ML_SYSTEM_DESIGN.md)** - ML system architecture
- **[archive/ml/SECURITY_SANDBOXING.md](archive/ml/SECURITY_SANDBOXING.md)** - Security threat model
- **[archive/ml/FUTURE_DIRECTIONS.md](archive/ml/FUTURE_DIRECTIONS.md)** - Future enhancements
- **[archive/ml/DERIVATIVE_FEATURES.md](archive/ml/DERIVATIVE_FEATURES.md)** - Derivative features design
- **[archive/ml/DERIVATIVE_FEATURES_QUICK_START.md](archive/ml/DERIVATIVE_FEATURES_QUICK_START.md)** - Quick start guide

### Historical Documentation
- **[archive/historical/FUNCTIONS_AS_DATA.md](archive/historical/FUNCTIONS_AS_DATA.md)** - Original DB-first design doc
- **[archive/historical/FEATURE_DISPATCHER.md](archive/historical/FEATURE_DISPATCHER.md)** - Dispatcher implementation
- **[archive/historical/DISPATCHER_IMPLEMENTATION_SUMMARY.md](archive/historical/DISPATCHER_IMPLEMENTATION_SUMMARY.md)** - Dispatcher summary
- **[archive/historical/PERFORMANCE_*.md](archive/historical/)** - Detailed performance optimization history
- **[archive/historical/FINAL_SUMMARY.md](archive/historical/FINAL_SUMMARY.md)** - Project milestone summary
- **[archive/historical/dev-journal.md](archive/historical/dev-journal.md)** - Development journal
- **[archive/historical/data-model.md](archive/historical/data-model.md)** - Original data model

### Bugfix Documentation
- **[archive/bugfixes/bugfix_chunk_not_found.md](archive/bugfixes/bugfix_chunk_not_found.md)** - TimescaleDB chunk issue
- **[archive/bugfixes/BUGFIX_FEATURES_COMPUTE.md](archive/bugfixes/BUGFIX_FEATURES_COMPUTE.md)** - Feature computation fixes
- **[archive/bugfixes/bugfix_writer_thread_deadlock.md](archive/bugfixes/bugfix_writer_thread_deadlock.md)** - Deadlock resolution
- **[archive/bugfixes/safety_improvements.md](archive/bugfixes/safety_improvements.md)** - Safety enhancements

## Contributing

When adding new documentation:

1. **User-facing docs** → Place in `docs/` root
2. **Design/architecture deep dives** → Consider if better in `archive/historical/`
3. **ML/future vision** → Place in `archive/ml/`
4. **Bugfix narratives** → Place in `archive/bugfixes/`
5. **Update this README** to maintain the index

Keep root docs/ clean and focused on what users need *now*.
