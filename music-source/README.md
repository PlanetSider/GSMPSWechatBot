# 本地音乐源脚本

将你有权使用的 LX Music 兼容脚本放在本目录。实际脚本、访问密钥和 source_order.txt 为部署本地资源，不纳入版本控制。

配置入口为 config.json 的 music.script_dir。可使用本地 source_order.txt 指定脚本文件名或 source_id 顺序。不要上传带密钥、账号、私人代理或混淆凭据的下载脚本；请分别确认第三方授权。

音乐源缺失时，相关插件会根据配置使用可用源/回退，不能保证任意歌曲可下载。服务适配代码位于 services/music/，不依赖将下载脚本公开到 Git。
