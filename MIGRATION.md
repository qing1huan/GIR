# 迁移与复现

本仓库的 `headless_v01` 是最小、确定性的 Game IR 参考实现，包含源码、测试和12个
已知开发示例。原始实验方案在 `docs/AI_GameIR_Cloud_Dot_Experiment_Plan.docx`；
原有根 README 与方案保持不变。本次发布没有添加完整实验工作区或其历史。

## 下载到 D:\GIR

在 GitHub 仓库页面点击 **Code → Download ZIP**。解压后，将 ZIP 中 `GIR-main`
目录的内容放入 `D:\GIR`，使 `D:\GIR\headless_v01\runtime.py` 存在。
如果该目录已有你的文件，先备份，不删除已有文件。也可在空目录使用：

```powershell
git clone https://github.com/qing1huan/GIR.git D:\GIR
```

## 运行（Python 3.12）

无需 pip 包、渲染引擎、网络服务、模型 API 或凭证。Windows PowerShell：

```powershell
Set-Location D:\GIR
$env:PYTHONUTF8 = '1'
py -3.12 -m headless_v01.verify --output .\results\headless-local
```

`PYTHONUTF8`使Python及其CLI子进程统一读写UTF-8。它只设置进程配置，不需要创建或
读取任何env文件。没有 `py` 启动器时，用指向 Python 3.12 的 `python` 替代。

仅运行测试：

```powershell
py -3.12 -m unittest discover -s headless_v01/tests -t . -v
```

Linux/macOS：

```bash
PYTHONUTF8=1 python3 -m headless_v01.verify --output ./results/headless-local
```

预期：63个唯一工程测试 PASS，已包含12个端到端示例；验收corpus为10 PASS、
0 FAIL、2 NOT RUN。输出含原始unit-tests.log、JSON/CSV矩阵、状态与控制journal、
CLI命令结果。验证器可从ZIP运行，Git未安装时source_commit=null是正常情况。

## 状态与来源

实现来源：`f1176ecda2cd8170bbe822e927c7ab610a4cd65f`。本次采用允许清单建立新的
公开发布提交，不推送原工程历史。核心实现、测试与示例保持来源提交的字节内容；
`verify.py`只移除了私人执行背景的报告元数据，改为中性的模型未运行说明。
文件SHA256和允许清单见 `PUBLICATION_MANIFEST.json`。

非敏感复现证据见 `headless_v01/evidence/public-release`。重复运行和生成trace不增加
独立成功样本；工程通过率不能当成被测AI成功率。所有示例均已知，不是隐藏holdout。

A09真实物理Backend变更与A12 JS/TS/Vitric公平adapter仍NOT RUN。IR仍未正式冻结，
AI运行0次，token和真实AI指标null。本次发布没有扩展玩法、引擎或模型实验。
