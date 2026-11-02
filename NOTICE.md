# 第三方来源与图片

本仓库的 MIT 许可证只覆盖本项目原创代码与文档，不替代第三方许可。

- zte-config-utility 作为固定 Git revision 的 Python 依赖安装，其实现与许可证保留在上游：[MIT LICENSE](https://github.com/mkst/zte-config-utility/blob/master/LICENSE)。
- Dropbear、glibc、GCC 运行库等由构建脚本从 Entware 获取。固定版本、原包 SHA256 与下载位置在 entware.lock.json 中。本仓库不再分发这些二进制或原厂测速组件。原始源码、补丁及构建资料见 [Entware](https://github.com/Entware/Entware) 及对应上游项目；若另外发布构建后的二进制，应履行各组件的许可证要求。
- telnetlib3、PyCryptodome 与 wcwidth 以 Python 依赖形式安装，按各自上游许可证使用。
- docs/images/telnet-guide.png 是根据本文实测流程生成的 AI 辅助说明图，供本教程分享使用；路由器外观为示意，不作为官方产品图片或品牌背书。图中未使用真实设备截图、配置备份、用户密码、用户密钥或设备标识。

SR7410 型号的公开 Key / IV 是配置编解码参数，不是任何一台用户设备的登录凭据。
