"""日志上报可靠性测试。"""
from __future__ import annotations

import queue as thread_queue
import sys

from slave.log_reporter import _TeeWriter


class TestTeeWriterDropCount:
    """队列满时应记录丢弃数而非静默丢弃。"""

    def test_no_drops_when_queue_has_space(self):
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=100)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("hello\n")
        assert writer.drop_count == 0
        assert q.qsize() == 1

    def test_drops_counted_when_queue_full(self):
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("line1\n")  # 入队成功
        writer.write("line2\n")  # 队列满，应丢弃并计数
        writer.write("line3\n")  # 再次丢弃
        assert writer.drop_count == 2

    def test_reset_drop_count(self):
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("line1\n")
        writer.write("line2\n")  # 丢弃 1 条
        assert writer.drop_count == 1
        count = writer.reset_drop_count()
        assert count == 1
        assert writer.drop_count == 0

    def test_drop_count_is_thread_safe(self):
        """并发写入时丢弃计数不丢失。"""
        import threading
        q: thread_queue.Queue[str] = thread_queue.Queue(maxsize=1)
        writer = _TeeWriter(None, q, "VM-01")
        writer.write("fill\n")  # 填满队列
        barrier = threading.Barrier(10)

        def write_many():
            barrier.wait()
            for _ in range(100):
                writer.write("x\n")

        threads = [threading.Thread(target=write_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert writer.drop_count == 1000  # 10 线程 × 100 次


class TestStderrCapture:
    """install() 应同时拦截 stdout 和 stderr。"""

    def test_install_replaces_stderr(self):
        from slave.log_reporter import LogReporter
        original_stderr = sys.stderr
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            assert sys.stderr is not original_stderr
            assert isinstance(sys.stderr, _TeeWriter)
        finally:
            reporter._restore_streams()

    def test_stderr_output_enters_queue_as_error(self):
        from slave.log_reporter import LogReporter
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            sys.stderr.write("something broke\n")
            msg = reporter._queue.get_nowait()
            assert "something broke" in msg
            assert "|ERROR|" in msg  # stderr 输出默认标记为 ERROR
        finally:
            reporter._restore_streams()

    def test_stderr_warn_keyword_stays_error(self):
        """stderr 中含 WARN 关键字时，级别仍应为 ERROR（is_stderr 是下限）。"""
        from slave.log_reporter import LogReporter
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            sys.stderr.write("WARNING: deprecation\n")
            msg = reporter._queue.get_nowait()
            assert "|ERROR|" in msg  # stderr 下限为 ERROR，不被 WARN 降级
        finally:
            reporter._restore_streams()

    def test_stop_restores_stderr(self):
        import asyncio  # noqa: E402

        from slave.log_reporter import LogReporter
        original_stderr = sys.stderr
        reporter = LogReporter("127.0.0.1", "VM-01")
        reporter.install()
        try:
            asyncio.run(reporter.stop())
            assert sys.stderr is original_stderr
        finally:
            # 兜底恢复，防止断言失败时污染后续测试
            reporter._restore_streams()
