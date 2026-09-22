MOKE / Zurich MFLI（DEV4435）兼容环境安装
===========================================

已确认的实验室配置：
- MFLI：DEV4435
- 仪器地址：192.168.69.78
- Data Server：TCP 8004
- 仪器端 LabOne：18.12.59443
- 当前 LabOne 网页界面能够正常显示 Demod 1 R 和 Phase，说明硬件链路正常。

安装步骤：
1. 关闭正在运行的 MOKE 主程序。
2. Windows 电脑保持联网。
3. 双击 INSTALL_MFLI_LABONE_18_12.bat。
4. 等待窗口显示 SUCCESS；若出现 Windows 网络/防火墙询问，允许访问。
5. 重新启动 Magneto-Electro-Optical Transport V3，再连接 Zurich MFLI。

脚本会做什么：
- 安装隔离的 64-bit Python 3.7.9 到：
  %LOCALAPPDATA%\NBB\MOKE_MFLI_PY37
- 从 PyPI 安装 NumPy 1.21.6 和精确匹配的 zhinst 18.12.59443；
- 校验 wheel SHA-256；
- 设置 MOKE_MFLI_PYTHON，并让程序自动发现该环境。

脚本不会做什么：
- 不会修改主 Python 3.11；
- 不会卸载或替换现有 zhinst；
- 不会改变 LabOne 仪器设置；
- 不会影响相机、VISA、磁体、LED 或电机模块。

说明：Python 3.7 已停止公开安全维护，因此这里仅作为旧版 LabOne 18.12 的
本地通信桥接环境使用，不用于浏览网页或运行其他日常程序。
