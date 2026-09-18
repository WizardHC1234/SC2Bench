# 寻路组件

SC2Bench 的独立寻路依赖，导入名为 `sc2pathlib`，不从旧项目回退加载，也不使用纯 Python 替代实现。

从 SC2Bench 根目录安装：

```bash
python -m pip install -e dependencies/sc2-pathlib
```

附带二进制适用于 Windows x64、CPython 3.9。Linux需编译匹配解释器的 native 文件，见 [Linux 构建](../../docs/installation.md#linux-构建与安装)。打包只包含当前解释器匹配的文件，wheel 带系统/ABI标签；Linux尚未验证编译和实机运行。

上游为 [DrInfy/sc2-pathlib](https://github.com/DrInfy/sc2-pathlib)，原 MIT 许可证保留在 LICENSE。当前 Windows 二进制 SHA256：

`cd6644c63f8f25b1d08b58c4465bd034fcae441f7372ee988e176a8f0d811427`

原二进制的源码提交和构建工具链未知，不宣称可由当前构建说明复现。
