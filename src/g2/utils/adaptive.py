from __future__ import annotations

from typing import Iterable, List, Sequence, Optional, Callable
import time
import multiprocessing

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


def chunked(seq: Sequence, size: int) -> List[Sequence]:
    size = max(1, size)
    return [seq[i : i + size] for i in range(0, len(seq), size)]


class AdaptiveLimiter:
    """
    Simple adaptive worker limiter.
    - starts at start_workers
    - on success (zero errors) increments by 1 up to max_workers
    - on error (>0 errors) halves (floored) down to min 1
    """

    def __init__(self, start_workers: int, max_workers: int):
        self.current = max(1, start_workers)
        self.max_workers = max(1, max_workers)

    def record_batch(self, errors: int) -> int:
        if errors == 0 and self.current < self.max_workers:
            self.current = min(self.max_workers, self.current + 1)
        elif errors > 0 and self.current > 1:
            self.current = max(1, self.current // 2)
        return self.current

    def value(self) -> int:
        return self.current


class ResourceAwareAdaptiveLimiter(AdaptiveLimiter):
    """
    Enhanced adaptive limiter that monitors system resources and adjusts
    max_workers dynamically based on available CPU, memory, and DB connections.

    Features:
    - Periodically checks system resources (CPU, memory)
    - Scales up when resources are available
    - Scales down when resources are constrained
    - Emits messages when scaling decisions are made
    - Still respects error-based adaptation from parent class
    """

    def __init__(
        self,
        start_workers: int,
        max_workers: int,
        available_db_connections: Optional[int] = None,
        writer_workers: int = 2,
        user_max_writer_workers: Optional[int] = None,
        memory_per_worker_mb: float = 125.0,
        memory_buffer_gb: float = 2.0,
        cpu_buffer: int = 2,
        db_buffer: int = 5,
        check_interval_seconds: float = 30.0,
        emit_func: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize resource-aware adaptive limiter.

        Args:
            start_workers: Initial number of workers
            max_workers: Absolute maximum workers (user-specified limit)
            available_db_connections: Total DB connections available (None = no limit)
            writer_workers: Initial number of writer threads per worker
            user_max_writer_workers: User's max limit for writer workers (None = auto)
            memory_per_worker_mb: Estimated memory per worker in MB
            memory_buffer_gb: Memory buffer to reserve for OS/other processes (GB)
            cpu_buffer: Number of CPU cores to reserve
            db_buffer: Number of DB connections to reserve
            check_interval_seconds: How often to check resources
            emit_func: Function to call for outputting messages (e.g., progress.emit)
        """
        super().__init__(start_workers, max_workers)

        self.user_max_workers = max_workers  # User's absolute limit
        self.available_db_connections = available_db_connections
        self.writer_workers = writer_workers
        self.user_max_writer_workers = user_max_writer_workers
        self.memory_per_worker_mb = memory_per_worker_mb
        self.memory_buffer_gb = memory_buffer_gb
        self.cpu_buffer = cpu_buffer
        self.db_buffer = db_buffer
        self.check_interval = check_interval_seconds
        self.emit = emit_func or (lambda msg: None)

        self.last_check_time = time.time()  # Initialize to current time
        self.last_resource_max = max_workers
        self.last_writer_workers = writer_workers

        # Do initial resource check
        self._update_resource_limits()

    def _calculate_optimal_workers_and_writers(self) -> tuple[int, int]:
        """
        Calculate optimal max workers and writer_workers based on current system resources.

        This performs an iterative optimization:
        1. Try different combinations of (max_workers, writer_workers)
        2. Find the combination that maximizes throughput while respecting resource limits
        3. Consider CPU, memory, and DB connection constraints

        Returns:
            Tuple of (optimal_max_workers, optimal_writer_workers)
        """
        # Start with baseline limits
        cpu_cores = multiprocessing.cpu_count()
        available_cpus = max(1, cpu_cores - self.cpu_buffer)

        # Get memory info
        available_gb = 10.0  # Default fallback
        if PSUTIL_AVAILABLE:
            try:
                mem = psutil.virtual_memory()
                available_gb = mem.available / (1024 ** 3)
            except Exception:
                pass

        usable_gb = max(0.5, available_gb - self.memory_buffer_gb)
        usable_mb = usable_gb * 1024

        # Get DB connection info
        available_db = self.available_db_connections
        if available_db is not None:
            available_db = max(0, available_db - self.db_buffer)

        # Try different writer_workers values (reasonable range: 1-8)
        best_score = 0
        best_max_workers = 1
        best_writer_workers = 1

        writer_range = range(1, 9) if self.user_max_writer_workers is None else [self.user_max_writer_workers]

        for test_writers in writer_range:
            # Calculate memory footprint per worker with this writer count
            memory_per_worker_total = self.memory_per_worker_mb + (test_writers * 2)

            # Memory-based limit
            memory_limited_workers = max(1, int(usable_mb / memory_per_worker_total))

            # DB connection limit (if applicable)
            db_limited_workers = memory_limited_workers
            if available_db is not None:
                connections_per_worker = 1 + test_writers
                db_limited_workers = max(1, available_db // connections_per_worker)

            # Take the tighter constraint
            max_workers_for_this_config = min(memory_limited_workers, db_limited_workers)

            # Cap at user's max
            max_workers_for_this_config = min(max_workers_for_this_config, self.user_max_workers)

            # CPU constraint: total threads shouldn't exceed available CPUs too much
            # Each worker has 1 compute thread + test_writers writer threads
            total_threads = max_workers_for_this_config * (1 + test_writers)
            if total_threads > available_cpus * 2:  # Allow 2x oversubscription
                # Scale back max_workers to fit CPU constraint
                max_workers_for_this_config = max(1, available_cpus * 2 // (1 + test_writers))

            # Score this configuration: max_workers * writer_workers
            # This approximates throughput (parallel stocks * parallel writes per stock)
            score = max_workers_for_this_config * test_writers

            if score > best_score:
                best_score = score
                best_max_workers = max_workers_for_this_config
                best_writer_workers = test_writers

        return (best_max_workers, best_writer_workers)

    def _update_resource_limits(self) -> bool:
        """
        Check current resources and update max_workers and writer_workers if needed.

        Returns:
            True if any limits changed, False otherwise
        """
        optimal_max, optimal_writers = self._calculate_optimal_workers_and_writers()

        changed = False

        # Check if max_workers changed
        if optimal_max != self.last_resource_max:
            old_max = self.last_resource_max
            self.max_workers = optimal_max
            self.last_resource_max = optimal_max

            # Emit message about resource-based scaling
            if optimal_max < old_max:
                self.emit(f"⚠️  Scaling down: max workers {old_max} → {optimal_max} (resource constraints)")
            else:
                self.emit(f"✓ Scaling up: max workers {old_max} → {optimal_max} (resources available)")

            # Also adjust current if it exceeds new max
            if self.current > self.max_workers:
                old_current = self.current
                self.current = self.max_workers
                self.emit(f"   Reducing active workers {old_current} → {self.current}")

            changed = True

        # Check if writer_workers changed
        if optimal_writers != self.last_writer_workers:
            old_writers = self.last_writer_workers
            self.writer_workers = optimal_writers
            self.last_writer_workers = optimal_writers

            # Emit message about writer worker scaling
            if optimal_writers < old_writers:
                self.emit(f"⚠️  Scaling down: writer workers {old_writers} → {optimal_writers} (resource constraints)")
            else:
                self.emit(f"✓ Scaling up: writer workers {old_writers} → {optimal_writers} (resources available)")

            changed = True

        return changed

    def record_batch(self, errors: int) -> int:
        """
        Record batch result and check if we should update resource limits.

        This is called after each batch, so it's a good place to periodically
        check resources without adding a separate monitoring thread.
        """
        # Check if it's time to re-evaluate resources
        current_time = time.time()
        if current_time - self.last_check_time >= self.check_interval:
            self._update_resource_limits()
            self.last_check_time = current_time

        # Call parent's error-based adaptation
        return super().record_batch(errors)

    def get_resource_info(self) -> dict:
        """
        Get current resource information for diagnostics.

        Returns:
            Dictionary with resource usage info
        """
        info = {
            "current_workers": self.current,
            "max_workers": self.max_workers,
            "user_max": self.user_max_workers,
            "writer_workers": self.writer_workers,
            "user_max_writer_workers": self.user_max_writer_workers,
        }

        if PSUTIL_AVAILABLE:
            try:
                mem = psutil.virtual_memory()
                cpu_percent = psutil.cpu_percent(interval=0.1)
                info["memory_available_gb"] = mem.available / (1024 ** 3)
                info["memory_percent_used"] = mem.percent
                info["cpu_percent"] = cpu_percent
            except Exception:
                pass

        return info

    def get_writer_workers(self) -> int:
        """
        Get current writer_workers value.

        Returns:
            Current number of writer workers per compute worker
        """
        return self.writer_workers
