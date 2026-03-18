"""LogReceiverThread._parse_line 单元测试"""

from master.app.core.log_receiver import LogEntry, LogReceiverThread


class MockSignal:
    """模拟 pyqtSignal，无需 Qt 事件循环"""

    def __init__(self):
        self.last_emit = None
        self.emit_count = 0

    def emit(self, *args):
        self.last_emit = args[0] if len(args) == 1 else args
        self.emit_count += 1


class TestLogReceiverParsing:
    def setup_method(self):
        self.receiver = LogReceiverThread(port=0)

    def test_parse_valid_log_line(self):
        self.receiver.log_received = MockSignal()
        self.receiver._parse_line("LOG|VM-01|12:30:01|INFO|服务就绪")
        assert self.receiver.log_received.last_emit is not None
        entry = self.receiver.log_received.last_emit
        assert isinstance(entry, LogEntry)
        assert entry.machine_name == "VM-01"
        assert entry.timestamp == "12:30:01"
        assert entry.level == "INFO"
        assert entry.content == "服务就绪"

    def test_parse_ignores_non_log(self):
        self.receiver.log_received = MockSignal()
        self.receiver._parse_line("NOTLOG|data")
        assert self.receiver.log_received.last_emit is None

    def test_parse_ignores_incomplete(self):
        self.receiver.log_received = MockSignal()
        self.receiver._parse_line("LOG|VM-01|12:30")
        assert self.receiver.log_received.last_emit is None

    def test_parse_empty_string(self):
        self.receiver.log_received = MockSignal()
        self.receiver._parse_line("")
        assert self.receiver.log_received.last_emit is None

    def test_parse_multiple_pipes_in_content(self):
        """日志内容中包含 | 分隔符"""
        self.receiver.log_received = MockSignal()
        self.receiver._parse_line("LOG|VM-02|08:00:00|WARN|错误|详细信息|更多")
        assert self.receiver.log_received.last_emit is not None
        entry = self.receiver.log_received.last_emit
        assert entry.machine_name == "VM-02"
        assert entry.level == "WARN"
        # split("|", 4) 保留后续所有内容
        assert entry.content == "错误|详细信息|更多"

    def test_parse_emits_count(self):
        self.receiver.log_received = MockSignal()
        self.receiver._parse_line("LOG|VM-01|10:00:00|INFO|第一条")
        self.receiver._parse_line("LOG|VM-01|10:00:01|INFO|第二条")
        assert self.receiver.log_received.emit_count == 2
