# Bemfa Cloud Home Assistant 集成

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)

[![hacs][hacsbadge]][hacs]
[![Community Forum][forum-shield]][forum]

_将 Home Assistant 中的设备同步到巴法云。_

**Bemfa Cloud 将 HA 中的设备同步到巴法云，让你可以通过巴法云、小爱同学等入口控制 HA 设备。**

简体中文 | [English](README_en.md)

## 功能特性

- **两种认证方式**：支持直接输入巴法云私钥，也支持 OAuth 登录
- **按需同步**：自由选择需要同步到巴法云的 HA 设备
- **远程控制**：通过巴法云、小爱同学等入口控制已同步的设备
- **状态同步**：HA 设备状态变化后，自动同步到巴法云
- **昵称和房间同步**：HA 中修改实体昵称、区域或区域名称后，会同步到巴法云设备昵称和房间
- **同步关系更稳定**：修改实体昵称或房间后，通常不会重复创建巴法云设备
- **兼容 BeHome**：自动跳过 BeHome 添加的设备，避免重复同步

## 支持的设备类型

支持插座、灯、风扇、传感器、空调、开关、窗帘、温控器、热水器、电视和空气净化器。脚本、自动化等也可作为开关同步，具体以配置页面中的可选设备为准。

## 安装方法

### HACS 安装（推荐）

1. 在 Home Assistant 中打开 HACS
2. 进入 **集成**
3. 点击 **浏览和下载存储库**
4. 搜索 **Bemfa Cloud**
5. 下载并重启 Home Assistant

### HACS 自定义仓库安装（开发/测试）

如果 HACS 商店暂未收录，或者需要测试开发版本：

1. 在 HACS 的右上角菜单中选择 **自定义存储库**
2. 填写存储库地址：`https://github.com/bemfa/bemfa_cloud_ha`
3. 类型选择 **集成**
4. 添加后搜索 **Bemfa Cloud** 并下载
5. 重启 Home Assistant

### 手动安装

1. 打开 Home Assistant 配置目录，也就是包含 `configuration.yaml` 的目录
2. 如果没有 `custom_components` 目录，请创建一个
3. 将本仓库的 `custom_components/bemfa_cloud` 复制到 HA 的 `custom_components` 目录中
4. 重启 Home Assistant

## 配置说明

如需同步到多个巴法云账号，可以重复添加 Bemfa Cloud 中枢。每个中枢绑定一个账号，并可单独选择要同步的 HA 实体。

### 私钥方式

1. 进入 **设置** -> **设备与服务** -> **添加集成**
2. 搜索 **Bemfa Cloud**
3. 选择 **私钥**
4. 输入巴法云用户私钥 `uid`
5. 登录后会直接显示设备列表，勾选需要同步的 HA 实体并保存。列表默认展示空调、窗帘、灯、开关等设备。
6. 以后点击 Bemfa Cloud 的 **配置** 即可修改勾选。已同步设备会默认勾选，取消勾选并保存即可停止同步；也可以全部取消。

### OAuth 方式

大多数用户建议使用私钥方式。如需通过 OAuth 登录，请先配置应用程序凭据。

1. 进入 **设置** -> **设备与服务** -> **助手** -> **应用程序凭据**
2. 创建新的应用程序凭据：
   - **名称**：`Bemfa Cloud`
   - **域**：`bemfa_cloud`
   - **客户端 ID**：`88ac425b4558463aa813aed1690db730`
   - **客户端密钥**：可填写任意安全字符串
3. 回到 **集成** 页面添加 **Bemfa Cloud**
4. 选择 OAuth 并完成授权

如果授权后跳转到 `homeassistant.local` 并提示无法访问，请在 Home Assistant 的 **设置** -> **系统** -> **网络** 中配置正确的 Home Assistant URL。Docker 本机测试可使用 `http://localhost:8123`，局域网访问可使用 `http://HA主机IP:8123`。

## 注意事项

- 本集成方向是 **HA -> 巴法云**
- BeHome 集成方向是 **巴法云 -> HA**
- 两个集成可以同时安装，但 Bemfa Cloud 会跳过 BeHome 生成的实体，避免重复同步或控制回环
- 同一个 HA 实体可以分别添加到不同 Bemfa Cloud 中枢，每个中枢的同步配置互相独立
- 修改部分 HA 实体的实体 ID 后，可能会在巴法云创建新的设备
- 从旧版本升级后，如果巴法云里出现重复设备，可在巴法云控制台手动删除旧设备

## 常见问题

### 同步失败怎么办？

请检查巴法云私钥是否正确、Home Assistant 是否可以访问外网，并查看 Home Assistant 日志中的 Bemfa Cloud 错误信息。

### 移除本地同步会删除巴法云设备吗？

不会。它只会停止本集成继续同步该实体，巴法云中的设备需要在巴法云控制台手动删除。

### 修改 HA 实体 ID 会怎样？

部分实体修改 ID 后，可能会在巴法云创建新的设备。如出现重复设备，可在巴法云控制台手动删除旧设备。

### 空调风速如何匹配？

自动风速、各档位以及低风、中风、高风会按 HA 空调支持的模式自动匹配，已有的自定义映射会继续保留。

## 支持与反馈

- [GitHub Issues](https://github.com/bemfa/bemfa_cloud_ha/issues)
- [Home Assistant 中文社区论坛](https://bbs.hassbian.com/)
- [Home Assistant 官方社区论坛](https://community.home-assistant.io/)

## 致谢

感谢 [larry-wong/bemfa](https://github.com/larry-wong/bemfa) 项目提供的参考和启发。

## 许可证

本项目使用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

---

[commits-shield]: https://img.shields.io/github/commit-activity/y/bemfa/bemfa_cloud_ha.svg?style=for-the-badge
[commits]: https://github.com/bemfa/bemfa_cloud_ha/commits/main
[hacs]: https://hacs.xyz
[hacsbadge]: https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/bemfa/bemfa_cloud_ha.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/bemfa/bemfa_cloud_ha.svg?style=for-the-badge
[releases]: https://github.com/bemfa/bemfa_cloud_ha/releases
