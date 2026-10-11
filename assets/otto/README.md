# 本地 Otto 音频资源

生成器代码位于 services/otto_generator.py，音频素材与索引为单独安装的本地资源，不随公开仓库分发。

启用 /otto 前，准备已获授权的资源，保持以下布局：

- tokens.json：拼音 token 名列表
- chinglish.json：字母/数字到拼音的映射
- ysdd.json：整句音频名与匹配文本的映射
- tokens/：拼音 WAV 片段，可包含变体子目录
- ysdd_tokens/：整句 MP3 片段

资源缺失时插件会报告错误，不会上传或猜测素材。请自行核实声音、人物及第三方素材使用/分发授权；不要将私人录音或群成员语料加入 Git。
