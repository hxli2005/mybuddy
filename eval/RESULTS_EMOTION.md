# 情绪 15 类识别评测结果(emotion_zh)

- 日期:2026-07-30
- 数据:`eval/data/emotion_zh.json`(v0-draft),n=300,15 类 x 20 条,每类 ≥6 条难例(克制表达/反话/混合情绪/省略主语短句)
- 模型:`deepseek/deepseek-v4-pro`(temperature=0,无上下文单条分类,脚本 `eval/emotion_eval.py`)
- classify 兜底失败(重试一次后仍无有效 category,按错计):188 条

> **口径:gold 待人工审校。** 本数据集的 gold 标签(valence/category/intensity_band)为 AI 起草的草案,尚未完成人工全量审校与分歧样本三人标注(方法见 `docs/DATASET_METHODOLOGY.md` §三.4)。下表数字应按"模型 vs 草案标注"的一致率解读,不等同于最终准确率;审校后需在同一集合上重跑并更新本文件。另:15 类人类标注亦难全一致,category 指标应对照人工 kappa 天花板解读,不按 100% 解读。

## 总体指标

| 指标 | 值 | Wilson 95% CI |
|---|---|---|
| valence 三分类准确率 | 36.0% (108/300) | 30.8% – 41.6% |
| category top-1 准确率(15 类) | 33.3% (100/300) | 28.2% – 38.8% |
| intensity 落带率(1-2 / 3 / 4-5) | 60.3% (181/300) | 54.7% – 65.7% |

## 难例 vs 易例

| 分层 | n | category top-1 | Wilson 95% CI | valence 准确率 |
|---|---|---|---|---|
| easy | 210 | 38.1% (80/210) | 31.8% – 44.8% | 40.0% |
| hard | 90 | 22.2% (20/90) | 14.9% – 31.8% | 26.7% |

## 逐类 category top-1

| category | n | category top-1 | valence 准确率 | intensity 落带率 |
|---|---|---|---|---|
| anxiety | 20 | 20.0% (4/20) | 25.0% | 60.0% |
| sadness | 20 | 45.0% (9/20) | 45.0% | 70.0% |
| anger | 20 | 35.0% (7/20) | 40.0% | 50.0% |
| fatigue | 20 | 25.0% (5/20) | 25.0% | 65.0% |
| loneliness | 20 | 20.0% (4/20) | 20.0% | 70.0% |
| stress | 20 | 15.0% (3/20) | 15.0% | 65.0% |
| guilt | 20 | 25.0% (5/20) | 30.0% | 70.0% |
| shame | 20 | 25.0% (5/20) | 30.0% | 70.0% |
| fear | 20 | 35.0% (7/20) | 40.0% | 60.0% |
| disappointment | 20 | 35.0% (7/20) | 35.0% | 55.0% |
| boredom | 20 | 35.0% (7/20) | 20.0% | 45.0% |
| calm | 20 | 50.0% (10/20) | 75.0% | 45.0% |
| joy | 20 | 40.0% (8/20) | 40.0% | 50.0% |
| gratitude | 20 | 60.0% (12/20) | 60.0% | 80.0% |
| excitement | 20 | 35.0% (7/20) | 40.0% | 50.0% |

## valence 混淆(gold 行 → pred 列)

| gold \ pred | negative | neutral | positive |
|---|---|---|---|
| negative | 65 | 155 | 0 |
| neutral | 0 | 15 | 4 |
| positive | 1 | 32 | 28 |

## 15x15 混淆矩阵(gold 行 → pred 列)

| gold \ pred | anxi | sadn | ange | fati | lone | stre | guil | sham | fear | disa | bore | calm | joy | grat | exci | none |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| anxiety | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 15 |
| sadness | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 11 |
| anger | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 11 |
| fatigue | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 15 |
| loneliness | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 16 |
| stress | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 16 |
| guilt | 1 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 14 |
| shame | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 14 |
| fear | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 12 |
| disappointment | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 13 |
| boredom | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 7 | 0 | 0 | 0 | 0 | 12 |
| calm | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 10 |
| joy | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 12 |
| gratitude | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 12 | 0 | 6 |
| excitement | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 7 | 11 |

(列名为类别前 4 字母,顺序:anxiety, sadness, anger, fatigue, loneliness, stress, guilt, shame, fear, disappointment, boredom, calm, joy, gratitude, excitement;none=兜底失败)

## top 混淆对

| gold | pred | 次数 |
|---|---|---|
| loneliness | (none) | 16 |
| stress | (none) | 16 |
| anxiety | (none) | 15 |
| fatigue | (none) | 15 |
| guilt | (none) | 14 |
| shame | (none) | 14 |
| disappointment | (none) | 13 |
| fear | (none) | 12 |
| boredom | (none) | 12 |
| joy | (none) | 12 |

## 误判样本(category 或 valence 不一致,供人工审校参考)

| id | difficulty | text | gold | pred | gold_val | pred_val |
|---|---|---|---|---|---|---|
| anx-01 | easy | 明天要当着全组做汇报,我现在心跳得特别快,PPT都翻不进去。 | anxiety | (none) | negative | neutral |
| anx-02 | easy | 体检报告出来了还没敢点开,越想越慌。 | anxiety | (none) | negative | neutral |
| anx-03 | easy | 一想到下个月房租还没着落,晚上翻来覆去睡不着。 | anxiety | (none) | negative | neutral |
| anx-04 | easy | 快面试了,手心一直冒汗,脑子里全是会被问倒的画面。 | anxiety | (none) | negative | neutral |
| anx-05 | easy | 我妈还没回我消息,平时她秒回的,总觉得要出什么事。 | anxiety | (none) | negative | neutral |
| anx-08 | easy | 领导说明天找我聊聊,一下午都心神不宁的。 | anxiety | (none) | negative | neutral |
| anx-09 | easy | 最近总是莫名心慌,坐着坐着就开始胡思乱想。 | anxiety | (none) | negative | neutral |
| anx-10 | easy | 一到晚上就开始想东想西,越想越睡不着,脑子停不下来。 | anxiety | (none) | negative | neutral |
| anx-11 | easy | 群里都在讨论裁员的事,我这几天饭都吃不太下。 | anxiety | (none) | negative | neutral |
| anx-12 | easy | 还有三天就出成绩了,紧张得不行。 | anxiety | (none) | negative | neutral |
| anx-13 | easy | 新环境还没适应,每天上班前都要在楼下缓半天才敢进去。 | anxiety | (none) | negative | neutral |
| anx-14 | easy | 总觉得自己哪里做错了什么,心里悬着一块石头。 | anxiety | (none) | negative | neutral |
| anx-15 | hard | 没事,就是这几天心里有点不踏实。 | anxiety | (none) | negative | neutral |
| anx-18 | hard | 呵,又要开家长会了,真期待啊。 | anxiety | disappointment | negative | negative |
| anx-19 | hard | 也没什么大事,就是心一直咚咚跳,静不下来。 | anxiety | (none) | negative | neutral |
| anx-20 | hard | 别问了,越问我越慌。 | anxiety | (none) | negative | neutral |
| sad-03 | easy | 今天路过以前常去的那家店,突然特别难过。 | sadness | (none) | negative | neutral |
| sad-05 | easy | 不知道为什么,今天就是特别想哭。 | sadness | (none) | negative | neutral |
| sad-06 | easy | 被最好的朋友删了好友,心里堵得难受。 | sadness | (none) | negative | neutral |
| sad-07 | easy | 电影看到一半哭得不行,想起好多事。 | sadness | (none) | negative | neutral |
| sad-11 | easy | 心情低落,干什么都开心不起来,已经一个星期了。 | sadness | (none) | negative | neutral |
| sad-12 | easy | 看到别人晒全家福,想起我爸,鼻子一酸。 | sadness | (none) | negative | neutral |
| sad-13 | easy | 今天挺难过的,具体也说不上来为什么。 | sadness | (none) | negative | neutral |
| sad-15 | hard | 没事,就是眼睛进沙子了。 | sadness | (none) | negative | neutral |
| sad-16 | hard | 挺好的,都挺好的,就我不太好。 | sadness | (none) | negative | neutral |
| sad-19 | hard | 心里空空的,说不上难过,就是不想说话。 | sadness | (none) | negative | neutral |
| sad-20 | hard | 行,都散了,挺好。 | sadness | (none) | negative | neutral |
| ang-01 | easy | 气死我了,方案又被他抢去邀功,当我不存在是吧! | anger | (none) | negative | neutral |
| ang-04 | easy | 室友半夜三点还在外放短视频,我真的忍到极限了。 | anger | (none) | negative | neutral |
| ang-06 | easy | 说好一起分摊,结账时他又装死,我火一下就上来了。 | anger | (none) | negative | neutral |
| ang-07 | easy | 客服踢了我一下午皮球,越想越来气。 | anger | (none) | negative | neutral |
| ang-08 | easy | 我妈又偷看我手机,还理直气壮,气得我摔门出来了。 | anger | (none) | negative | neutral |
| ang-09 | easy | 有点烦,今天开会又被他阴阳怪气了几句。 | anger | (none) | negative | neutral |
| ang-11 | easy | 凭什么加班的是我,升职的是他? | anger | (none) | negative | neutral |
| ang-12 | easy | 被冤枉拿了东西,解释半天没人信,委屈又愤怒。 | anger | (none) | negative | neutral |
| ang-13 | easy | 他开车别我一下还冲我竖中指,血压直接拉满。 | anger | (none) | negative | neutral |
| ang-15 | hard | 我没生气,我就是想知道他脑子里在想什么。 | anger | calm | negative | neutral |
| ang-16 | hard | 行啊,真行,不愧是你。 | anger | disappointment | negative | negative |
| ang-18 | hard | 懒得说了,说多了都是气。 | anger | (none) | negative | neutral |
| ang-20 | hard | 没事,你继续,当我空气好了。 | anger | (none) | negative | neutral |
| fat-01 | easy | 连着加了一周班,今天到家倒床上就不想动了。 | fatigue | (none) | negative | neutral |
| fat-02 | easy | 眼睛都睁不开了,可手上的活还没干完。 | fatigue | (none) | negative | neutral |
| fat-03 | easy | 带娃一天,腰都直不起来,只想瘫着。 | fatigue | (none) | negative | neutral |
| fat-04 | easy | 最近老是犯困,咖啡都压不住了。 | fatigue | (none) | negative | neutral |
| fat-05 | easy | 今天走了两万步,腿已经不是我的了。 | fatigue | (none) | negative | neutral |
| fat-06 | easy | 熬了两个大夜赶完标书,现在脑子是浆糊。 | fatigue | (none) | negative | neutral |
| fat-08 | easy | 这周三个通宵,我感觉身体被掏空了。 | fatigue | (none) | negative | neutral |
| fat-09 | easy | 就是干什么都提不起劲,睡多久都缓不过来。 | fatigue | (none) | negative | neutral |
| fat-11 | easy | 有点累,今天先不聊太多了。 | fatigue | (none) | negative | neutral |
| fat-12 | easy | 身体像灌了铅,爬三层楼歇了两次。 | fatigue | (none) | negative | neutral |
| fat-14 | easy | 今天课排得太满,现在只想闭眼十分钟。 | fatigue | (none) | negative | neutral |
| fat-15 | hard | 没事,就是有点撑不住了。 | fatigue | (none) | negative | neutral |
| fat-18 | hard | 撑不住了,先躺会。 | fatigue | (none) | negative | neutral |
| fat-19 | hard | 玩得挺开心,就是回来路上差点睡过站。 | fatigue | (none) | negative | neutral |
| fat-20 | hard | 电量告急,今天别派活了。 | fatigue | (none) | negative | neutral |
| lon-01 | easy | 一个人在这个城市第三年了,生病了都没人递杯水。 | loneliness | (none) | negative | neutral |
| lon-02 | easy | 周五晚上,朋友圈都在聚会,我对着外卖发呆。 | loneliness | (none) | negative | neutral |
| lon-03 | easy | 想找人说说话,翻遍通讯录不知道该打给谁。 | loneliness | (none) | negative | neutral |
| lon-04 | easy | 搬来新城市两个月,还没交到一个朋友。 | loneliness | (none) | negative | neutral |
| lon-06 | easy | 今天生日,到现在只有银行发了条祝福短信。 | loneliness | (none) | negative | neutral |
| lon-08 | easy | 深夜睡不着,感觉全世界都睡了,就我醒着。 | loneliness | (none) | negative | neutral |
| lon-09 | easy | 群里聊得热火朝天,我发的消息没人接。 | loneliness | (none) | negative | neutral |
| lon-10 | easy | 老公出差半个月,孩子睡了以后家里安静得吓人。 | loneliness | (none) | negative | neutral |
| lon-11 | easy | 有点孤单,想养只猫陪我。 | loneliness | (none) | negative | neutral |
| lon-12 | easy | 到了新学校,午饭都是一个人吃的。 | loneliness | (none) | negative | neutral |
| lon-14 | easy | 认识的人很多,能说心里话的一个都没有。 | loneliness | (none) | negative | neutral |
| lon-15 | hard | 没事,习惯一个人了。 | loneliness | (none) | negative | neutral |
| lon-16 | hard | 热闹是他们的,我什么也没有。 | loneliness | (none) | negative | neutral |
| lon-17 | hard | 聚会挺开心的,散场之后突然特别空。 | loneliness | (none) | negative | neutral |
| lon-19 | hard | 就是那种,人群里也觉得只有自己的感觉。 | loneliness | (none) | negative | neutral |
| lon-20 | hard | 屋子太安静了,连冰箱的声音都算个伴。 | loneliness | (none) | negative | neutral |
| str-01 | easy | 三个deadline全堆在这周,我快被压垮了。 | stress | (none) | negative | neutral |
| str-02 | easy | 房贷、孩子学费、爸妈体检,全压在我一个人身上。 | stress | (none) | negative | neutral |
| str-03 | easy | 下周连着四门考试,复习计划根本排不开。 | stress | (none) | negative | neutral |
| str-04 | easy | 老板今天又加了两个需求,还都是明天要的。 | stress | (none) | negative | neutral |
| str-06 | easy | 项目上线倒计时,组里能干活的就剩我一个。 | stress | (none) | negative | neutral |
| str-08 | easy | 感觉喘不过气,事情一件接一件停不下来。 | stress | (none) | negative | neutral |
| str-09 | easy | 每个月一到还款日我就头皮发麻。 | stress | (none) | negative | neutral |
| str-10 | easy | 家里催婚,工作催活,两头夹着我。 | stress | (none) | negative | neutral |
| str-12 | easy | 业绩考核月底就要交,现在看到报表就胃疼。 | stress | (none) | negative | neutral |
| str-13 | easy | 最近事情有点多,总觉得脑子里绷着根弦。 | stress | (none) | negative | neutral |
| str-14 | easy | 白天上班晚上照顾老人,我快转不动了。 | stress | (none) | negative | neutral |
| str-15 | hard | 还好,就是最近连喘口气的功夫都没有。 | stress | (none) | negative | neutral |
| str-16 | hard | 忙,别的没什么。 | stress | boredom | negative | neutral |
| str-17 | hard | 挺充实的呢,充实到凌晨一点。 | stress | (none) | negative | neutral |
| str-18 | hard | 项目拿下来是好事,就是接下来三个月没得睡了。 | stress | (none) | negative | neutral |
| str-19 | hard | 扛得住,再扛扛就过去了。 | stress | (none) | negative | neutral |
| str-20 | hard | 这日子过得跟打仗似的。 | stress | (none) | negative | neutral |
| gui-01 | easy | 答应陪女儿去动物园又放了鸽子,她哭了,我心里特别不是滋味。 | guilt | (none) | negative | neutral |
| gui-02 | easy | 跟我妈吵架说了狠话,现在特别后悔。 | guilt | (none) | negative | neutral |
| gui-03 | easy | 朋友创业找我帮忙,我拒绝了,总觉得欠他的。 | guilt | (none) | negative | neutral |
| gui-04 | easy | 忘了给爸爸订生日蛋糕,他嘴上说没事,我愧疚死了。 | guilt | (none) | negative | neutral |
| gui-06 | easy | 养的花被我忘在阳台晒死了,对不起它。 | guilt | (none) | negative | neutral |
| gui-07 | easy | 昨天心情不好冲男朋友发火了,其实他什么都没做错。 | guilt | (none) | negative | neutral |
| gui-08 | easy | 借了同学的笔记弄丢了,不知道怎么开口。 | guilt | (none) | negative | neutral |
| gui-11 | easy | 闺蜜的秘密被我说漏嘴了,现在特别自责。 | guilt | (none) | negative | neutral |
| gui-12 | easy | 狗子在家等了我一天,它扑上来的时候我眼泪差点下来。 | guilt | (none) | negative | neutral |
| gui-13 | easy | 有点对不起室友,昨晚打呼吵得她没睡好。 | guilt | (none) | negative | neutral |
| gui-15 | hard | 没什么,就是觉得那件事我也有份。 | guilt | (none) | negative | neutral |
| gui-16 | hard | 都怪我,行了吧。 | guilt | (none) | negative | neutral |
| gui-18 | hard | 要是当时我在场就好了。 | guilt | (none) | negative | neutral |
| gui-19 | hard | 我这当妈的,真是称职得很。 | guilt | (none) | negative | neutral |
| gui-20 | hard | 睡前又把那件事翻出来想了一遍。 | guilt | anxiety | negative | negative |
| sha-03 | easy | 被老师当众念了成绩,倒数第二,丢死人了。 | shame | (none) | negative | neutral |
| sha-04 | easy | 裤子在地铁上开线了,一路捂着走回家,尴尬到抠脚。 | shame | (none) | negative | neutral |
| sha-05 | easy | 唱K跑调被人录下来发群里了,我不想上班了。 | shame | (none) | negative | neutral |
| sha-08 | easy | 把吐槽老板的话发到工作群了,现在头皮发麻。 | shame | anxiety | negative | negative |
| sha-09 | easy | 健身房动作不标准被教练大声纠正,周围人都在看。 | shame | (none) | negative | neutral |
| sha-10 | easy | 简历上的项目被面试官当场戳穿,我恨不得立刻消失。 | shame | (none) | negative | neutral |
| sha-11 | easy | 有点丢脸,今天课上答非所问被笑了。 | shame | (none) | negative | neutral |
| sha-12 | easy | 让全公司看到我哭,太丢人了。 | shame | (none) | negative | neutral |
| sha-13 | easy | T恤穿反了一整天,居然没一个人提醒我。 | shame | (none) | negative | neutral |
| sha-14 | easy | 妈在家长群里晒我小时候的糗照,同学都看到了。 | shame | (none) | negative | neutral |
| sha-15 | hard | 没事,就是不太想再见到那群人了。 | shame | (none) | negative | neutral |
| sha-16 | hard | 挺光彩的,全场就我一个没通过。 | shame | (none) | negative | neutral |
| sha-17 | hard | 大家都说没事,可我还是抬不起头。 | shame | (none) | negative | neutral |
| sha-18 | hard | 只想找个地缝钻进去。 | shame | (none) | negative | neutral |
| sha-19 | hard | 从那以后我再没在会上开过口。 | shame | (none) | negative | neutral |
| fea-01 | easy | 楼道的灯坏了,黑漆漆的,我站在门口不敢上去。 | fear | (none) | negative | neutral |
| fea-02 | easy | 半夜听到客厅有声音,吓得我抱着被子不敢动。 | fear | (none) | negative | neutral |
| fea-04 | easy | 电梯刚才突然抖了一下,我现在腿还是软的。 | fear | (none) | negative | neutral |
| fea-05 | easy | 看完那个恐怖片,到现在不敢关灯睡觉。 | fear | (none) | negative | neutral |
| fea-10 | easy | 有人半夜敲门,猫眼里却没人,我吓得报了警。 | fear | (none) | negative | neutral |
| fea-11 | easy | 有点怕明天的手术,虽然医生说是小手术。 | fear | (none) | negative | neutral |
| fea-12 | easy | 飞机遇到强颠簸,那几分钟我手死死抓着扶手。 | fear | (none) | negative | neutral |
| fea-14 | easy | 走夜路总觉得后面有人跟着,不敢回头。 | fear | (none) | negative | neutral |
| fea-15 | hard | 没事,就是现在还不太敢一个人待着。 | fear | (none) | negative | neutral |
| fea-17 | hard | 挺刺激的哈,吓得我差点哭出来。 | fear | (none) | negative | neutral |
| fea-18 | hard | 先别关灯,就一会儿。 | fear | (none) | negative | neutral |
| fea-19 | hard | 检查结果明天出,今晚估计是睡不成了。 | fear | anxiety | negative | negative |
| fea-20 | hard | 那条路我现在都绕着走。 | fear | (none) | negative | neutral |
| dis-01 | easy | 准备了三个月的比赛,初赛就被刷了。 | disappointment | (none) | negative | neutral |
| dis-03 | easy | 考研成绩出来了,差两分,又是差两分。 | disappointment | (none) | negative | neutral |
| dis-04 | easy | 满心欢喜拆开快递,实物跟图片差太远了。 | disappointment | (none) | negative | neutral |
| dis-06 | easy | 攒了好久钱去的网红餐厅,难吃得想退钱。 | disappointment | (none) | negative | neutral |
| dis-08 | easy | 面试等了两周,今天等来一封感谢信。 | disappointment | (none) | negative | neutral |
| dis-13 | easy | 抽奖抽了个谢谢参与,意料之中吧。 | disappointment | (none) | negative | neutral |
| dis-14 | easy | 兴冲冲跑去打卡,结果店早就关了。 | disappointment | (none) | negative | neutral |
| dis-15 | hard | 行吧,真是太棒了。 | disappointment | (none) | negative | neutral |
| dis-16 | hard | 没关系,反正也不是第一次了。 | disappointment | (none) | negative | neutral |
| dis-17 | hard | 也没多期待,就是提前一周就把票买好了。 | disappointment | (none) | negative | neutral |
| dis-18 | hard | 呵,果然是这样。 | disappointment | (none) | negative | neutral |
| dis-19 | hard | 挺替他开心的,虽然那个名额本来说好是我的。 | disappointment | (none) | negative | neutral |
| dis-20 | hard | 就这样吧,不指望了。 | disappointment | (none) | negative | neutral |
| bor-01 | easy | 好无聊啊,刷手机刷到手机都嫌我了。 | boredom | (none) | negative | neutral |
| bor-02 | easy | 放假第五天,躺得我人都要发霉了。 | boredom | boredom | negative | neutral |
| bor-04 | easy | 一个下午了,群里一条消息都没有,无聊死了。 | boredom | (none) | negative | neutral |
| bor-07 | easy | 今天闲得有点没意思,没什么特别的事。 | boredom | (none) | negative | neutral |
| bor-08 | easy | 电视翻了一圈,一个想看的都没有。 | boredom | (none) | negative | neutral |
| bor-09 | easy | 值班八小时,一个电话都没有,人快坐傻了。 | boredom | (none) | negative | neutral |
| bor-10 | easy | 游戏也玩腻了,剧也追完了,不知道干嘛。 | boredom | (none) | negative | neutral |
| bor-11 | easy | 隔离在酒店第三天,墙纸的花纹我都背下来了。 | boredom | (none) | negative | neutral |
| bor-12 | easy | 退休以后天天就是遛弯买菜,闲得心里发慌。 | boredom | (none) | negative | neutral |
| bor-13 | easy | 无聊到把手机相册从头翻到尾。 | boredom | (none) | negative | neutral |
| bor-14 | easy | 通勤俩小时,车上无聊得能把广告牌背下来。 | boredom | (none) | negative | neutral |
| bor-15 | hard | 挺好的,又是平平无奇躺了一天。 | boredom | boredom | negative | neutral |
| bor-16 | hard | 闲着呢,闲得都有点想上班了。 | boredom | boredom | negative | neutral |
| bor-17 | hard | 没事干,你陪我聊会儿呗。 | boredom | boredom | negative | neutral |
| bor-18 | hard | 日子太安稳了,安稳得有点没意思。 | boredom | (none) | negative | neutral |
| bor-19 | hard | 就那样呗,一天跟一天一个样。 | boredom | (none) | negative | neutral |
| bor-20 | hard | 又是刷一天手机的一天。 | boredom | disappointment | negative | negative |
| cal-01 | easy | 今天没什么特别的,按部就班上了个班。 | calm | (none) | neutral | neutral |
| cal-02 | easy | 晚饭后在河边散了会儿步,风挺舒服的。 | calm | calm | neutral | positive |
| cal-03 | easy | 泡了杯茶,坐在阳台看了会儿云。 | calm | calm | neutral | positive |
| cal-05 | easy | 冥想完十分钟,脑子清净多了。 | calm | (none) | neutral | neutral |
| cal-07 | easy | 雨下了一天,在家听着雨声看书,挺静的。 | calm | (none) | neutral | neutral |
| cal-08 | easy | 事情都处理完了,现在心里挺踏实的。 | calm | (none) | positive | neutral |
| cal-11 | easy | 现在坐在窗边,外面在下小雨,挺安静的。 | calm | (none) | neutral | neutral |
| cal-12 | easy | 考完了,出分前反而心里挺平静的。 | calm | (none) | neutral | neutral |
| cal-13 | easy | 最近作息规律了,心也跟着稳了下来。 | calm | calm | neutral | positive |
| cal-14 | easy | 刚遛完狗回来,晚风一吹,心里挺静的。 | calm | (none) | neutral | neutral |
| cal-16 | hard | 风波总算过去了,现在就想安安静静待几天。 | calm | (none) | neutral | neutral |
| cal-17 | hard | 也说不上开心还是不开心,就是很平静。 | calm | (none) | neutral | neutral |
| cal-19 | hard | 吵完架冷静下来了,现在心里反而挺静的。 | calm | (none) | neutral | neutral |
| cal-20 | hard | 无风无浪,岁月静好那种。 | calm | calm | neutral | positive |
| joy-01 | easy | 今天offer到手了!是我最想去的那家! | joy | (none) | positive | neutral |
| joy-02 | easy | 女儿第一次开口叫妈妈,我高兴得快哭了。 | joy | (none) | positive | neutral |
| joy-03 | easy | 跟老朋友撸串聊到半夜,笑得肚子疼。 | joy | (none) | positive | neutral |
| joy-04 | easy | 减肥终于见效了,今天秤上少了四斤! | joy | (none) | positive | neutral |
| joy-06 | easy | 抢到演唱会门票了!前排!我现在还在飘! | joy | (none) | positive | neutral |
| joy-07 | easy | 今天做的红烧肉一次成功,好吃到自己都惊了。 | joy | (none) | positive | neutral |
| joy-10 | easy | 路上捡到一只超粘人的小猫,已经决定收编了。 | joy | (none) | positive | neutral |
| joy-12 | easy | 好久没这么开心过了,今天全程嘴角压不下来。 | joy | (none) | positive | neutral |
| joy-16 | hard | 完蛋,今天笑太多,脸都有点酸了。 | joy | (none) | positive | neutral |
| joy-17 | hard | 挺开心的,虽然也有点舍不得毕业。 | joy | (none) | positive | neutral |
| joy-18 | hard | 成了成了,真的成了! | joy | (none) | positive | neutral |
| joy-20 | hard | 破天气热得冰淇淋都化了,不过海边是真的好玩。 | joy | (none) | positive | neutral |
| gra-04 | easy | 老师帮我把推荐信改到凌晨,我都不知道怎么谢她。 | gratitude | (none) | positive | neutral |
| gra-10 | easy | 朋友把他攒的面试题全给我了,还陪我模拟了一遍。 | gratitude | (none) | positive | neutral |
| gra-15 | hard | 也没什么,就是有人把伞塞给我,自己淋着雨跑了。 | gratitude | (none) | positive | neutral |
| gra-16 | hard | 嘴上没说什么,心里都记着呢。 | gratitude | disappointment | positive | negative |
| gra-17 | hard | 又欠你一顿饭了。 | gratitude | calm | positive | neutral |
| gra-18 | hard | 都怪你啊,搞得我都不知道怎么还这份人情。 | gratitude | (none) | positive | neutral |
| gra-19 | hard | 说不感动是假的,虽然我当场憋着没哭。 | gratitude | (none) | positive | neutral |
| gra-20 | hard | 这年头还有人记得我生日,行吧,记你一辈子。 | gratitude | (none) | positive | neutral |
| exc-02 | easy | 我的稿子要出版了!!我现在整个人在房间里转圈! | excitement | (none) | positive | neutral |
| exc-04 | easy | 刚抢到新机首发,一发货我就打算蹲门口等快递。 | excitement | (none) | positive | neutral |
| exc-06 | easy | 偶像官宣要来我们城市开演唱会了啊啊啊啊! | excitement | (none) | positive | neutral |
| exc-07 | easy | 第一次当伴娘,裙子都试好了,好期待! | excitement | (none) | positive | neutral |
| exc-09 | easy | 医生说下周就能拆石膏了,我终于能去打球了! | excitement | joy | positive | positive |
| exc-12 | easy | 宝宝下个月就是预产期了,婴儿房都布置好了。 | excitement | (none) | positive | neutral |
| exc-14 | easy | 人生第一次坐飞机,现在就开始兴奋了。 | excitement | (none) | positive | neutral |
| exc-15 | hard | 也还好,就是闹钟定了五个,怕睡过头错过高铁。 | excitement | (none) | positive | neutral |
| exc-16 | hard | 淡定淡定,不就是明天见爱豆嘛,啊不行我淡定不了。 | excitement | (none) | positive | neutral |
| exc-17 | hard | 完了,兴奋得根本睡不着。 | excitement | (none) | positive | neutral |
| exc-18 | hard | 明天!就是明天了! | excitement | (none) | positive | neutral |
| exc-19 | hard | 又激动又怕搞砸,毕竟是我第一次牵头这么大的项目。 | excitement | (none) | positive | neutral |
| exc-20 | hard | 嘴上说随便,身体倒是很诚实地提前两小时就到了。 | excitement | calm | positive | neutral |
