# TriangleAlpha - 统一运行/打包入口

UV ?= uv
PYTHONPATH ?= src
MASTER_ENTRY ?= master.main
SLAVE_ENTRY ?= slave.main
MASTER_SPEC ?= master.spec
SLAVE_SPEC ?= slave.spec
PYTHON_SRC ?= src
TESTS_DIR ?= tests

# VM 部署配置
VM_HOST ?= administrator@100.117.195.115
VM_DEPLOY_DIR ?= C:/Users/Administrator/Desktop/TriangleAlphaOOOOO
VM_BUILD_DIR ?= $(VM_DEPLOY_DIR)/_build
VM_PYTHON ?= C:\\Python312\\python.exe

.PHONY: help deps sync lint fmt typecheck test check \
	run run-master run-slave \
	package package-master package-slave \
	deploy-slave \
	clean

deps: ## 安装 uv（未安装时）
	@command -v $(UV) >/dev/null 2>&1 || (echo "安装 uv..." && curl -LsSf https://astral.sh/uv/install.sh | sh)

sync: deps ## 同步项目依赖
	$(UV) sync

lint: sync ## Ruff 检查
	$(UV) run ruff check $(PYTHON_SRC) $(TESTS_DIR)

fmt: sync ## Ruff 自动格式化并修复
	$(UV) run ruff format $(PYTHON_SRC) $(TESTS_DIR)
	$(UV) run ruff check --fix $(PYTHON_SRC) $(TESTS_DIR)

typecheck: sync ## Mypy 类型检查
	PYTHONPATH=$(PYTHONPATH) $(UV) run mypy $(PYTHON_SRC)

test: sync ## 运行测试
	PYTHONPATH=$(PYTHONPATH) $(UV) run pytest $(TESTS_DIR) -v

check: lint typecheck test ## 完整检查

run: run-master ## 启动主控端

run-master: sync ## 运行主控端 GUI
	PYTHONPATH=$(PYTHONPATH) $(UV) run python -m $(MASTER_ENTRY)

run-slave: sync ## 运行被控端 GUI
	PYTHONPATH=$(PYTHONPATH) $(UV) run python -m $(SLAVE_ENTRY)

package: package-master package-slave ## 打包全部产物

package-master: sync ## 打包主控端
	PYTHONPATH=$(PYTHONPATH) $(UV) run pyinstaller --clean --noconfirm $(MASTER_SPEC)

package-slave: sync ## 打包被控端
	PYTHONPATH=$(PYTHONPATH) $(UV) run pyinstaller --clean --noconfirm $(SLAVE_SPEC)

deploy-slave: ## 构建并部署被控端到 VM（生产版，无控制台）
	@echo ">>> 上传源码到 VM..."
	ssh $(VM_HOST) "mkdir $(VM_BUILD_DIR)\\src\\common 2>nul & mkdir $(VM_BUILD_DIR)\\src\\slave\\resource 2>nul & echo OK"
	scp slave.spec pyproject.toml $(VM_HOST):"$(VM_BUILD_DIR)/"
	scp src/common/__init__.py src/common/protocol.py src/common/models.py $(VM_HOST):"$(VM_BUILD_DIR)/src/common/"
	scp src/slave/*.py $(VM_HOST):"$(VM_BUILD_DIR)/src/slave/"
	scp -r src/slave/resource/ $(VM_HOST):"$(VM_BUILD_DIR)/src/slave/resource/"
	@echo ">>> 停止旧进程..."
	-ssh $(VM_HOST) "taskkill /F /IM TriangleAlpha-Slave.exe 2>nul"
	@echo ">>> 在 VM 上构建..."
	ssh $(VM_HOST) "cd $(VM_BUILD_DIR) && $(VM_PYTHON) -m PyInstaller --clean --noconfirm slave.spec"
	@echo ">>> 部署..."
	ssh $(VM_HOST) "copy /Y \"$(VM_BUILD_DIR)\\dist\\TriangleAlpha-Slave.exe\" \"$(VM_DEPLOY_DIR)\\TriangleAlpha-Slave.exe\""
	@echo ">>> 启动..."
	ssh $(VM_HOST) "schtasks /Create /TN StartSlave /TR \"$(VM_DEPLOY_DIR)\\TriangleAlpha-Slave.exe\" /SC ONCE /ST 00:00 /F && schtasks /Run /TN StartSlave && schtasks /Delete /TN StartSlave /F"
	@echo ">>> 部署完成！"

clean: ## 清理构建与缓存目录
	rm -rf build dist .ruff_cache .mypy_cache .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

help: ## 显示可用命令
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
