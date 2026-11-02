# 反馈与贡献

请先确认型号、完整固件版本与 README 的适用范围。其他固件需要独立验证，不能只修改型号字符串就声明支持。

Issue 只需要提供型号、固件版本、Python 版本、出错阶段和经过人工脱敏的错误文字。不要上传 config.bin、解码 XML、原始配置报告、完整网络截图、SSH 密钥、known_hosts、远程访问令牌、Wi-Fi 名称、序列号或 MAC。

提交前运行：

~~~bash
python -m unittest discover -s tests -v
python scripts/check_public_tree.py
git diff --check
~~~

配置格式相关测试只能使用合成数据。新增设备支持应提供公开型号参数、格式检查、失败保护、回退方法和设备验收结果，不能提交真实设备配置作为测试夹具。

第三方软件固定版本与哈希只根据可信上游原包更新，同时检查等长重定位的原始哈希、命中次数以及 PTY 兼容性；不得将哈希不一致改成警告后继续执行。
