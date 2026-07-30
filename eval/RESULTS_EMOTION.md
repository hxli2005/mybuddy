# 情绪 15 类识别评测结果(emotion_zh)

- 日期:2026-07-31
- 数据:`eval/data/emotion_zh.json`(v0-draft),n=300,15 类 x 20 条,每类 ≥6 条难例(克制表达/反话/混合情绪/省略主语短句)
- 模型:`deepseek/deepseek-v4-pro`(temperature=0,无上下文单条分类,脚本 `eval/emotion_eval.py`)
- classify 兜底失败(重试一次后仍无有效 category,按错计):0 条

> **口径:gold 待人工审校。** 本数据集的 gold 标签(valence/category/intensity_band)为 AI 起草的草案,尚未完成人工全量审校与分歧样本三人标注(方法见 `docs/DATASET_METHODOLOGY.md` §三.4)。下表数字应按"模型 vs 草案标注"的一致率解读,不等同于最终准确率;审校后需在同一集合上重跑并更新本文件。另:15 类人类标注亦难全一致,category 指标应对照人工 kappa 天花板解读,不按 100% 解读。

## 总体指标

| 指标 | 值 | Wilson 95% CI |
|---|---|---|
| valence 三分类准确率 | 86.0% (258/300) | 81.6% – 89.5% |
| category top-1 准确率(15 类) | 85.0% (255/300) | 80.5% – 88.6% |
| intensity 落带率(1-2 / 3 / 4-5) | 62.0% (186/300) | 56.4% – 67.3% |

## 难例 vs 易例

| 分层 | n | category top-1 | Wilson 95% CI | valence 准确率 |
|---|---|---|---|---|
| easy | 210 | 92.4% (194/210) | 88.0% – 95.3% | 92.9% |
| hard | 90 | 67.8% (61/90) | 57.6% – 76.5% | 70.0% |

## 逐类 category top-1

| category | n | category top-1 | valence 准确率 | intensity 落带率 |
|---|---|---|---|---|
| anxiety | 20 | 90.0% (18/20) | 95.0% | 75.0% |
| sadness | 20 | 85.0% (17/20) | 90.0% | 65.0% |
| anger | 20 | 85.0% (17/20) | 90.0% | 55.0% |
| fatigue | 20 | 90.0% (18/20) | 85.0% | 45.0% |
| loneliness | 20 | 90.0% (18/20) | 100.0% | 80.0% |
| stress | 20 | 80.0% (16/20) | 80.0% | 60.0% |
| guilt | 20 | 70.0% (14/20) | 90.0% | 60.0% |
| shame | 20 | 75.0% (15/20) | 95.0% | 60.0% |
| fear | 20 | 75.0% (15/20) | 90.0% | 60.0% |
| disappointment | 20 | 100.0% (20/20) | 90.0% | 60.0% |
| boredom | 20 | 90.0% (18/20) | 40.0% | 75.0% |
| calm | 20 | 100.0% (20/20) | 75.0% | 85.0% |
| joy | 20 | 80.0% (16/20) | 100.0% | 60.0% |
| gratitude | 20 | 90.0% (18/20) | 85.0% | 50.0% |
| excitement | 20 | 75.0% (15/20) | 85.0% | 40.0% |

## valence 混淆(gold 行 → pred 列)

| gold \ pred | negative | neutral | positive |
|---|---|---|---|
| negative | 189 | 27 | 4 |
| neutral | 0 | 14 | 5 |
| positive | 2 | 4 | 55 |

## 15x15 混淆矩阵(gold 行 → pred 列)

| gold \ pred | anxi | sadn | ange | fati | lone | stre | guil | sham | fear | disa | bore | calm | joy | grat | exci | none |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| anxiety | 18 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| sadness | 0 | 17 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 |
| anger | 0 | 0 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 0 | 0 | 0 | 0 |
| fatigue | 0 | 0 | 0 | 18 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| loneliness | 0 | 0 | 0 | 0 | 18 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| stress | 1 | 0 | 0 | 2 | 0 | 16 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| guilt | 2 | 1 | 1 | 0 | 0 | 0 | 14 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 0 |
| shame | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| fear | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 0 | 1 | 0 | 0 | 1 | 0 |
| disappointment | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| boredom | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 18 | 1 | 0 | 0 | 0 | 0 |
| calm | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 20 | 0 | 0 | 0 | 0 |
| joy | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 16 | 0 | 3 | 0 |
| gratitude | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 18 | 0 | 0 |
| excitement | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 | 0 | 15 | 0 |

(列名为类别前 4 字母,顺序:anxiety, sadness, anger, fatigue, loneliness, stress, guilt, shame, fear, disappointment, boredom, calm, joy, gratitude, excitement;none=兜底失败)

## top 混淆对

| gold | pred | 次数 |
|---|---|---|
| shame | disappointment | 4 |
| joy | excitement | 3 |
| loneliness | disappointment | 2 |
| stress | fatigue | 2 |
| guilt | anxiety | 2 |
| fear | anxiety | 2 |
| excitement | joy | 2 |
| excitement | calm | 2 |
| anxiety | stress | 1 |
| anxiety | guilt | 1 |

## 误判样本(category 或 valence 不一致,供人工审校参考)

| id | difficulty | text | gold | pred | gold_val | pred_val |
|---|---|---|---|---|---|---|
| anx-11 | easy | 群里都在讨论裁员的事,我这几天饭都吃不太下。 | anxiety | stress | negative | negative |
| anx-14 | easy | 总觉得自己哪里做错了什么,心里悬着一块石头。 | anxiety | guilt | negative | negative |
| anx-17 | hard | 挺期待明天的,就是手一直在抖。 | anxiety | anxiety | negative | neutral |
| sad-15 | hard | 没事,就是眼睛进沙子了。 | sadness | sadness | negative | neutral |
| sad-18 | hard | 就……挺想她的。 | sadness | loneliness | negative | negative |
| sad-19 | hard | 心里空空的,说不上难过,就是不想说话。 | sadness | boredom | negative | neutral |
| sad-20 | hard | 行,都散了,挺好。 | sadness | disappointment | negative | negative |
| ang-15 | hard | 我没生气,我就是想知道他脑子里在想什么。 | anger | calm | negative | neutral |
| ang-16 | hard | 行啊,真行,不愧是你。 | anger | disappointment | negative | negative |
| ang-20 | hard | 没事,你继续,当我空气好了。 | anger | boredom | negative | neutral |
| fat-05 | easy | 今天走了两万步,腿已经不是我的了。 | fatigue | fatigue | negative | neutral |
| fat-13 | easy | 忙完这阵子又是下一阵子,感觉永远歇不上。 | fatigue | stress | negative | negative |
| fat-16 | hard | 还行,就是三天没怎么睡了。 | fatigue | fatigue | negative | neutral |
| fat-19 | hard | 玩得挺开心,就是回来路上差点睡过站。 | fatigue | joy | negative | positive |
| lon-06 | easy | 今天生日,到现在只有银行发了条祝福短信。 | loneliness | disappointment | negative | negative |
| lon-09 | easy | 群里聊得热火朝天,我发的消息没人接。 | loneliness | disappointment | negative | negative |
| str-08 | easy | 感觉喘不过气,事情一件接一件停不下来。 | stress | anxiety | negative | negative |
| str-14 | easy | 白天上班晚上照顾老人,我快转不动了。 | stress | fatigue | negative | negative |
| str-16 | hard | 忙,别的没什么。 | stress | stress | negative | neutral |
| str-17 | hard | 挺充实的呢,充实到凌晨一点。 | stress | calm | negative | neutral |
| str-18 | hard | 项目拿下来是好事,就是接下来三个月没得睡了。 | stress | fatigue | negative | neutral |
| str-19 | hard | 扛得住,再扛扛就过去了。 | stress | stress | negative | neutral |
| gui-08 | easy | 借了同学的笔记弄丢了,不知道怎么开口。 | guilt | anxiety | negative | negative |
| gui-12 | easy | 狗子在家等了我一天,它扑上来的时候我眼泪差点下来。 | guilt | gratitude | negative | positive |
| gui-16 | hard | 都怪我,行了吧。 | guilt | anger | negative | negative |
| gui-17 | hard | 升职挺开心的,就是想到踩的是他的方案,心里咯噔一下。 | guilt | joy | negative | positive |
| gui-18 | hard | 要是当时我在场就好了。 | guilt | sadness | negative | negative |
| gui-20 | hard | 睡前又把那件事翻出来想了一遍。 | guilt | anxiety | negative | negative |
| sha-08 | easy | 把吐槽老板的话发到工作群了,现在头皮发麻。 | shame | anxiety | negative | negative |
| sha-13 | easy | T恤穿反了一整天,居然没一个人提醒我。 | shame | disappointment | negative | negative |
| sha-15 | hard | 没事,就是不太想再见到那群人了。 | shame | disappointment | negative | negative |
| sha-16 | hard | 挺光彩的,全场就我一个没通过。 | shame | disappointment | negative | negative |
| sha-19 | hard | 从那以后我再没在会上开过口。 | shame | disappointment | negative | negative |
| sha-20 | hard | 哈哈哈,大型社死现场,别提了。 | shame | shame | negative | neutral |
| fea-16 | hard | 不怕不怕,就是手到现在还在抖。 | fear | anxiety | negative | negative |
| fea-17 | hard | 挺刺激的哈,吓得我差点哭出来。 | fear | excitement | negative | positive |
| fea-18 | hard | 先别关灯,就一会儿。 | fear | calm | negative | neutral |
| fea-19 | hard | 检查结果明天出,今晚估计是睡不成了。 | fear | anxiety | negative | negative |
| fea-20 | hard | 那条路我现在都绕着走。 | fear | sadness | negative | negative |
| dis-13 | easy | 抽奖抽了个谢谢参与,意料之中吧。 | disappointment | disappointment | negative | neutral |
| dis-19 | hard | 挺替他开心的,虽然那个名额本来说好是我的。 | disappointment | disappointment | negative | neutral |
| bor-01 | easy | 好无聊啊,刷手机刷到手机都嫌我了。 | boredom | boredom | negative | neutral |
| bor-06 | easy | 工作就是复制粘贴,做了三年,一眼望到头。 | boredom | disappointment | negative | negative |
| bor-07 | easy | 今天闲得有点没意思,没什么特别的事。 | boredom | boredom | negative | neutral |
| bor-08 | easy | 电视翻了一圈,一个想看的都没有。 | boredom | boredom | negative | neutral |
| bor-10 | easy | 游戏也玩腻了,剧也追完了,不知道干嘛。 | boredom | boredom | negative | neutral |
| bor-11 | easy | 隔离在酒店第三天,墙纸的花纹我都背下来了。 | boredom | boredom | negative | neutral |
| bor-13 | easy | 无聊到把手机相册从头翻到尾。 | boredom | boredom | negative | neutral |
| bor-14 | easy | 通勤俩小时,车上无聊得能把广告牌背下来。 | boredom | boredom | negative | neutral |
| bor-15 | hard | 挺好的,又是平平无奇躺了一天。 | boredom | calm | negative | neutral |
| bor-16 | hard | 闲着呢,闲得都有点想上班了。 | boredom | boredom | negative | neutral |
| bor-17 | hard | 没事干,你陪我聊会儿呗。 | boredom | boredom | negative | neutral |
| bor-18 | hard | 日子太安稳了,安稳得有点没意思。 | boredom | boredom | negative | neutral |
| bor-19 | hard | 就那样呗,一天跟一天一个样。 | boredom | boredom | negative | neutral |
| cal-02 | easy | 晚饭后在河边散了会儿步,风挺舒服的。 | calm | calm | neutral | positive |
| cal-05 | easy | 冥想完十分钟,脑子清净多了。 | calm | calm | neutral | positive |
| cal-07 | easy | 雨下了一天,在家听着雨声看书,挺静的。 | calm | calm | neutral | positive |
| cal-13 | easy | 最近作息规律了,心也跟着稳了下来。 | calm | calm | neutral | positive |
| cal-14 | easy | 刚遛完狗回来,晚风一吹,心里挺静的。 | calm | calm | neutral | positive |
| joy-01 | easy | 今天offer到手了!是我最想去的那家! | joy | excitement | positive | positive |
| joy-06 | easy | 抢到演唱会门票了!前排!我现在还在飘! | joy | excitement | positive | positive |
| joy-18 | hard | 成了成了,真的成了! | joy | excitement | positive | positive |
| joy-19 | hard | 没什么大事,就是路过的小学生冲我笑了下,一天都挺好。 | joy | calm | positive | positive |
| gra-15 | hard | 也没什么,就是有人把伞塞给我,自己淋着雨跑了。 | gratitude | calm | positive | neutral |
| gra-16 | hard | 嘴上没说什么,心里都记着呢。 | gratitude | anger | positive | negative |
| gra-18 | hard | 都怪你啊,搞得我都不知道怎么还这份人情。 | gratitude | gratitude | positive | neutral |
| exc-09 | easy | 医生说下周就能拆石膏了,我终于能去打球了! | excitement | joy | positive | positive |
| exc-12 | easy | 宝宝下个月就是预产期了,婴儿房都布置好了。 | excitement | joy | positive | positive |
| exc-15 | hard | 也还好,就是闹钟定了五个,怕睡过头错过高铁。 | excitement | calm | positive | neutral |
| exc-19 | hard | 又激动又怕搞砸,毕竟是我第一次牵头这么大的项目。 | excitement | anxiety | positive | negative |
| exc-20 | hard | 嘴上说随便,身体倒是很诚实地提前两小时就到了。 | excitement | calm | positive | neutral |
