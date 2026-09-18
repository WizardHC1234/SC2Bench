# Sharpy 维护副本

SC2Bench 内部独立维护的 Sharpy 库，负责底层控制，不依赖 Commander 或相邻目录。原 MIT 许可证保留在 LICENSE。

从 SC2Bench 根目录安装：

```bash
python -m pip install -e dependencies/sc2-pathlib -e dependencies/sharpy-sc2 -e '.[sharpy]'
```

平台通过 `sharpy.runtime_config.configure(config_provider, version_provider)` 提供配置，不查找顶层 config.py。库自身也有包内默认配置。

任务、观测和指令适配在 `sc2bench_env.backends.sharpy` 中维护；记录由平台统一负责，默认关闭额外数据写入。完整安装见 [安装文档](../../docs/installation.md)。
