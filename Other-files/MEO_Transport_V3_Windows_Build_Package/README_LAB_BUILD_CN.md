# Magneto-Electro-Optical Transport V3——Windows 单文件 EXE 构建包（3.2.0）

本文件夹于 2026-08-27 冻结当前开发源码，用于在 Windows 10/11 x64 电脑上自动编译、
离线验证并安装一个不暴露 `.py` 源码的单文件：

```text
Magneto_Electro_Optical_Transport_V3.exe
```

构建方式沿用实验室已经成功使用的 WP810/MOKE 组合：
**CPython 3.11.9 x64 + Nuitka 4.1.3 + MinGW64 + one-file**。

## 编译后的使用方式

构建成功后，日常启动主程序只需要该 EXE，不需要再复制 Python 源码、虚拟环境
或 pip 包。全部最终交付物会统一生成到：

```text
本包\V3_Compiled_Output\
```

并在桌面创建：

```text
Magneto-Electro-Optical Transport V3.lnk
```

## 必须区分：Python 包与厂商运行环境

Nuitka 会把 Python 解释器及本项目所需 Python 包收进单个 EXE。以下硬件厂商
驱动或服务不能合法、可靠地塞进 EXE，使用对应模块的电脑仍需预先安装：

| 模块 | Windows 电脑仍需安装/运行 |
|---|---|
| PCO Camera | PCO Camera SDK/驱动；构建脚本会另外检查 EXE 内的 PCO Python 原生 DLL |
| Keithley/SR830/2182A/6221/2410/2400 | NI-VISA 及实际 GPIB/USB 驱动 |
| Thorlabs K10CR1 | 与控制器位数匹配的 Thorlabs Kinesis x64 |
| Zurich MFLI | 对应 LabOne；旧版 18.12 可用 LabOne 自带 .NET API，必要时运行本包的 Python 3.7 桥接安装器 |
| PPMS1 QDInstrument Remote | ThinkPad 上安装与 LabVIEW 匹配的 `QDInstrument.dll`，实验室 PPMS 电脑运行 MultiVu + QDInstrument Server |
| PPMS1 MultiPyVu（可选） | PPMS 主机必须运行 MultiVu 与 MultiPyVu Server；客户端 Python 包已打入 EXE |
| LED/电机/磁体串口 | 对应 FTDI/CH340/USB-Serial 驱动 |

因此，“只用 EXE”是指应用和 Python 依赖只需一个 EXE；它不能替代仪器驱动、
LabOne、Kinesis、NI-VISA、MultiVu 或 PPMS 服务端。

## 编译前准备

1. Windows 10/11 x64。
2. 建议预留至少 10 GB 空间，并保持首次构建可以联网。
3. 把整个构建包解压到本地磁盘；不要直接在 U 盘、网络盘或同步盘编译。
4. 关闭正在运行的旧版 `Magneto_Electro_Optical_Transport_V3.exe`。
5. 不必提前安装 Python；脚本找不到 64-bit Python 3.11 时会优先使用
   `winget`，失败后下载并验证 Python 3.11.9 x64 官方安装器。

## 一键编译和安装

双击：

```text
BUILD_AND_INSTALL_V3.bat
```

脚本会自动：

1. 定位或安装 64-bit Python 3.11；
2. 创建独立 `.build_venv`；
3. 安装固定版本的构建和运行依赖；
4. 检查 PCO DLL、Python 语法及离线仪器逻辑；
5. 运行 28 项离线回归测试（开发版 27 项加发布身份检查）；
6. 使用 Nuitka + MinGW64 生成 one-file EXE；
7. 检查 Nuitka PCO payload 并实际运行编译后 PCO 原生库自检；
8. 将 EXE、SHA-256、PCO 自检日志和 Nuitka 编译报告统一发布到 `V3_Compiled_Output`；
9. 创建桌面快捷方式；
10. 逐字节比较安装副本并生成 SHA-256 文件。

只有最后出现以下内容才算构建成功：

```text
BUILD AND INSTALL COMPLETED SUCCESSFULLY
Offline instrument tests: PASS
PCO native DLL payload and load self-test: PASS
```

第一次构建通常需要 30–90 分钟。Nuitka 在约 95% 长时间没有刷新通常是在执行
C 编译或链接；只要没有明确的 `ERROR`，且任务管理器仍存在 Python、SCons 或
GCC 进程，就继续等待。

## 成功后的文件

```text
单文件程序：本包\V3_Compiled_Output\Magneto_Electro_Optical_Transport_V3.exe
校验文件：本包\V3_Compiled_Output\Magneto_Electro_Optical_Transport_V3.exe.sha256
桌面快捷方式：Magneto-Electro-Optical Transport V3.lnk
PCO自检日志：本包\V3_Compiled_Output\MEO_Transport_V3_pco_self_test.txt
编译报告：本包\V3_Compiled_Output\MEO_Transport_V3_compilation_report.xml
```

`build` 目录仅保留 Nuitka 中间文件，便于失败诊断，不属于最终交付物。若只在
已经装好厂商驱动/运行环境的同一台实验电脑上使用，复制整个
`V3_Compiled_Output` 文件夹即可；桌面快捷方式会直接指向其中的 EXE。

## 真机验收边界

编译、离线回归和 PCO DLL 自检都不等于全部仪器已通过真机验收。首次使用该 EXE
时必须从安全设置开始逐模块确认：

- 相机只连接、不输出；确认 Live 与保存功能；
- LED 从 0 开始；
- 电机确认机械路径、限位和方向；
- 2410/6221 从最低电压/电流开始并核对限压；
- 磁体切换物理拨挡前，两路都必须归零；
- PPMS1 首先只读取温度和磁场，再用接近当前值的小范围设定验证写入；
- 关闭程序后确认 2410、6221、KEPCO 等输出均符合预期安全状态。

## 常见问题

- **找不到 Python 3.11**：保持联网后重新运行 BAT；也可手动执行
  `winget install --id Python.Python.3.11 -e --scope user`。
- **PCO DLL 检查失败**：不要使用残留 EXE；确认 `pco==2.6.0` 安装完整后重试。
- **发布文件夹覆盖失败**：关闭所有从 `V3_Compiled_Output` 打开的 EXE、日志或报告后重试。
- **EXE 启动但仪器连接失败**：检查厂商驱动、资源地址、服务端、网络和仪器占用；
  不要重新安装 Python 包，因为它们已经在 EXE 内。
- **杀毒软件隔离**：不要直接恢复或加入白名单；记录检测名、路径和实际 SHA-256，
  交由 IT 安全人员审核。无商业代码签名的 one-file EXE 可能触发启发式检测。

## 源码对应关系

本包的 `Magneto_Electro_Optical_Transport_V3.py` 是从
`../Magneto_Electro_Optical_Transport_NH_dev.py` 冻结的发布副本，仅把界面版本号
从 V1 改为 V3，并把 PCO 自检日志改为本包命名。原始 dev 源码没有被覆盖。

本次 3.2.0 修订把 PCO 图像全流程统一为逆时针旋转 90°，同步更新物理角标和
100× FOV 标记；MOKE 参数日志新增完整扫场设置以及扫描开始/导出时的光源和
磁体缓存状态。扫描开始快照在活动通道正式生效后生成，避免日志误记为未运行；
导出同时记录实测电流/磁场范围及 ROI 采样统计。保存过程不新增串口/VISA 查询，
硬件协议、地址、软件限值和仪器延时没有在本次构建整理中改变。

冻结源码 SHA-256：

冻结源码 SHA-256 以包内 `BUILD_PACKAGE_VERSION.txt` 和
`RELEASE_CHECKSUMS_SHA256.txt` 为准。
