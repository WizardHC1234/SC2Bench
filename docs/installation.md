# 安装与打包

SC2Bench 已按标准 Python 包组织：`pyproject.toml` 声明安装信息，`src/sc2bench_env/` 是核心库，安装后提供 `sc2bench` 命令。实机依赖项目内维护的 Sharpy 和寻路组件，不加载旧项目文件。

## 核心安装

Python 3.9 及以上，不需要 SC2 或 native 组件：

```bash
python -m pip install -e .
python -m sc2bench_env doctor --backend fake
```

开发测试加装 `.[dev]`；使用 LLM 示例加装 `.[llm]`。模型客户端属于外部 Agent，不是核心环境依赖。

多局并行加装 `.[parallel]`（cloudpickle 和 psutil）；`.[llm]` 与 `.[dev]` 已包含这两项。默认串行不需要并行依赖。

## Windows 实机

当前已验证 Windows x64、CPython 3.9。先安装 SC2 客户端和地图，再从项目根安装：

```bash
python -m pip install -e dependencies/sc2-pathlib -e dependencies/sharpy-sc2 -e '.[sharpy,llm]'
python -m pip check
python -m sc2bench_env doctor --suite benchmarks/terran_pilot.json
```

同一条命令显式安装两个本地维护库，避免使用索引中不同实现的同名包。附带寻路二进制仅适用于 Python 3.9 Windows x64，不能直接用于其他系统或 Python 版本。

客户端不在默认位置时，设置 `SC2PATH` 为安装目录；地图放在其 `Maps/` 下或第一层子目录。游戏资源不随 Python 包分发。

## Linux 构建与安装

已提供构建方式，尚未做 Linux 编译和实机验证。准备 64 位 CPython 3.9、对应开发头文件、Git、C/C++ 编译工具和 Rust/Cargo。从项目根执行：

```bash
set -euo pipefail
python3.9 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
export PYO3_PYTHON="$(python -c 'import sys; print(sys.executable)')"

native_source="$(mktemp -d)"
git clone https://github.com/DrInfy/sc2-pathlib.git "$native_source"
git -C "$native_source" checkout --detach c23fd7387cd8d0898d35bbd9758f7cfb9fbe6f29
rust_host="$(rustc -vV | sed -n 's/^host: //p')"
cargo build --release --locked --target "$rust_host" \
  --manifest-path "$native_source/Cargo.toml" --target-dir "$native_source/target"

extension_suffix="$(python -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
install -m 755 "$native_source/target/$rust_host/release/libsc2pathlib.so" \
  "dependencies/sc2-pathlib/sc2pathlib/sc2pathlib$extension_suffix"

python -m pip install -e dependencies/sc2-pathlib -e dependencies/sharpy-sc2 -e '.[sharpy,llm]'
python -m sc2bench_env doctor --backend fake
```

这是固定上游源码的构建候选，不是现有 Windows 二进制的已知来源。仅复制 native 文件，保留项目中的 Python 包装代码；安装后还需验证实际兼容性，不回退到 Windows 文件或纯 Python 替代实现。

客户端安装好后设置 `SC2PATH`，运行 `python -m sc2bench_env doctor --suite benchmarks/terran_pilot.json`。路径和地图名称注意大小写。

## 构建普通安装包

核心包可单独打包：

```bash
python -m pip wheel --no-deps --wheel-dir dist .
python -m pip install dist/sc2bench_env-0.1.0-py3-none-any.whl
```

实机需三个包。使用匹配本机 native 的 Python 3.9 环境：

```bash
python -m pip wheel --no-deps --wheel-dir dist . dependencies/sc2-pathlib dependencies/sharpy-sc2
python -m pip install --find-links dist 'sc2bench-env[sharpy,llm]==0.1.0'
```

安装后无需源码目录。native 包使用解释器/系统标签，不是通用 wheel。完全离线还需提前下载所有第三方依赖。当前未发布到 PyPI，不假设直接从公网安装能获得本维护版本。

## 自检与配置

`python -m sc2bench_env doctor` 检查依赖、客户端和地图，不启动对局。只测核心用 `--backend fake`，指定地图用 `--map`。错误返回1，配置错误返回2，自检通过不替代实机测试。

默认读取包内配置，无需额外配置文件。需要自定义时，可设置绝对路径环境变量 `SC2BENCH_CONFIG`；源码安装也可自行创建根目录 `config.ini` 覆盖默认值。不需要工作目录里的 `config.py`。输出位置用 `python -m sc2bench_env paths` 查看，详见 [Agent 接入](agent_integration.md)。
