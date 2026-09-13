# Testing

当环境不允许 pytest 写入系统临时目录时，运行测试必须统一复用项目内的临时根目录：

```powershell
pytest --basetemp .pist/pytest
```

当需要验证打包产物时，必须统一输出到项目内的构建目录：

```powershell
uv build --out-dir .pist/build
```

当需要编写临时诊断、检查或全量扫描脚本时，放入 `.pist/scripts`。

# References

需要拉取公开参考代码库时，统一浅克隆到 `.pist/references/`；该目录仅用于研究和对照，不纳入项目包或测试输入。

# Pyright

项目使用 Pyright 1.1.411 的实验性 Sentinel 支持。在包含 Sentinel 的递归类型别名中，未显式标注的容器字面量会重新展开并推断值类型，因而产生不必要的类型错误。

以下述类型为例：
```python
from collections.abc import Mapping

from typing_extensions import Sentinel


UNKNOWN = Sentinel("UNKNOWN")
type Value = int | Mapping[str, Value] | UNKNOWN
```

最小可复现示例：
```python
def ok(values: dict[str, Value], key: str) -> None:
    x = values[key]
    values[key] = x

def error(values: dict[str, Value], key: str) -> None:
    x = {key: values[key]}
    values[key] = x[key]
    # error:
    # Type "UNKNOWN" is not assignable to type "UNKNOWN"
```

显式标注目标类型可以修复：
```python
def ok(values: dict[str, Value], key: str) -> None:
    x: dict[str, Value] = {key: values[key]}
    values[key] = x[key]
```

仅在这些方式无法表达真实类型时，才在边界处使用最小范围的 `cast`。
