import { limitTranscriptByteLength } from '~/lib/openai/getSmallSizeTranscripts'
import { VideoConfig } from '~/lib/types'
import { DEFAULT_LANGUAGE, PROMPT_LANGUAGE_MAP } from '~/utils/constants/language'

interface PromptConfig {
  language?: string
  sentenceCount?: string
  shouldShowTimestamp?: boolean
}

export function getExamplePrompt() {
  return {
    input: `标题: "【BiliGPT】AI 自动总结 B站 视频内容，GPT-3 智能提取并总结字幕"
视频字幕: "2.06 - 哈喽哈喽 这里是机密的频道 今天给大家整个活叫哔哩哔哩gp t  6.71 - 选择插着gp t的爆火 作为软件工程师的我也按捺不住 去需要把哔哩哔哩的url贴进来  21.04 - 然后你就点击一键总结 稍等片刻 你就可以获得这样一份精简的总结`,
    output: `视频概述：BiliGPT 是一款自动总结B站视频内容的 AI 工具

- 2.06 - 作为软件工程师的我按捺不住去开发了 BiliGPT
- 21.04 - 只需要粘贴哔哩哔哩的URL，一键总结为精简内容`,
  }
}

export function getSystemPrompt(promptConfig: PromptConfig) {
  // [gpt-3-youtube-summarizer/main.py at main · tfukaza/gpt-3-youtube-summarizer](https://github.com/tfukaza/gpt-3-youtube-summarizer/blob/main/main.py)
  console.log('prompt config: ', promptConfig)
  const { language = '中文', sentenceCount = '5', shouldShowTimestamp } = promptConfig
  // @ts-ignore
  const enLanguage = PROMPT_LANGUAGE_MAP[language]
  // 我希望你是一名专业的视频内容编辑，帮我用${language}总结视频的内容精华。请你将视频字幕文本进行总结（字幕中可能有错别字，如果你发现了错别字请改正），然后以无序列表的方式返回，不要超过5条。记得不要重复句子，确保所有的句子都足够精简，清晰完整，祝你好运！
  const betterPrompt = `I want you to act as an educational content creator. You will help students summarize the essence of the video in ${enLanguage}. Please summarize the video subtitles (there may be typos in the subtitles, please correct them) and return them in an unordered list format. Please do not exceed ${sentenceCount} items, and make sure not to repeat any sentences and all sentences are concise, clear, and complete. Good luck!`
  // const timestamp = ' ' //`（类似 10:24）`;
  // 我希望你是一名专业的视频内容编辑，帮我用${language}总结视频的内容精华。请先用一句简短的话总结视频梗概。然后再请你将视频字幕文本进行总结（字幕中可能有错别字，如果你发现了错别字请改正），在每句话的最前面加上时间戳${timestamp}，每句话开头只需要一个开始时间。请你以无序列表的方式返回，请注意不要超过5条哦，确保所有的句子都足够精简，清晰完整，祝你好运！
  const promptWithTimestamp = `I would like you to act as a professional video content editor. You will help students summarize the essence of the video in ${enLanguage}. Please start by summarizing the whole video in one short sentence (there may be typos in the subtitles, please correct them). Then, please summarize the video subtitles, each subtitle should has the start timestamp (e.g. 12.4 -) so that students can select the video part. Please return in an unordered list format, make sure not to exceed ${sentenceCount} items and all sentences are concise, clear, and complete. Good luck!`

  return shouldShowTimestamp ? promptWithTimestamp : betterPrompt
}
export function getUserSubtitlePrompt(title: string, transcript: any, videoConfig: VideoConfig) {
  const videoTitle = title?.replace(/\n+/g, ' ').trim()
  const videoTranscript = limitTranscriptByteLength(transcript).replace(/\n+/g, ' ').trim()
  const language = videoConfig.outputLanguage || DEFAULT_LANGUAGE
  const sentenceCount = videoConfig.sentenceNumber || 7
  const emojiTemplateText = videoConfig.showEmoji ? '[Emoji] ' : ''
  const emojiDescriptionText = videoConfig.showEmoji ? 'Choose an appropriate emoji for each bullet point. ' : ''
  const shouldShowAsOutline = Number(videoConfig.outlineLevel) > 1
  const wordsCount = videoConfig.detailLevel ? (Number(videoConfig.detailLevel) / 100) * 2 : 15
  const outlineTemplateText = shouldShowAsOutline ? `\n    - Child points` : ''
  const outlineDescriptionText = shouldShowAsOutline
    ? `Use the outline list, which can have a hierarchical structure of up to ${videoConfig.outlineLevel} levels. `
    : ''
  const prompt = `你是一名专业的视频内容编辑，专门从事网红带货视频。你的任务是提供极其详细和全面的修改建议，将视频转化为爆款视频。

【重要语言要求】你必须使用中文（简体中文）进行所有回复。所有标题、小标题、内容、说明、示例都必须使用中文。禁止使用英文或其他语言。如果看到英文术语，请用中文表达。

首先，深入分析视频内容：
- 识别产品、品牌和关键卖点
- 理解目标受众及其痛点
- 分析当前脚本结构和内容流程
- 评估运镜、视觉元素和呈现风格

然后按照以下结构提供详细的修改建议：

## 脚本修改建议

### 1. 开场钩子（3秒抓注意力）
提供具体的开场话术，使用痛点攻击、好奇心钩子或情感触发。包括：
- 具有情感冲击力的具体开场短语
- 如何介绍产品和今天的福利
- 互动元素（例如，让观众评论特定数字）

### 2. 互动引流与受众激活
建议视频中具体的互动时刻：
- 向观众提问（例如，"有睡眠问题的扣1"）
- 如何营造社区感和共同体验
- 激活不同受众群体的方式

### 3. 产品故事化与卖点拆解
针对每个关键卖点，提供：
- 使用 FABE 框架的详细解释（功能、优势、利益、证据）
- 具体场景和演示
- 视觉呈现建议（例如，"按压枕头展示回弹"）
- 情感连接点和用户利益
- 强化可信度的数字和数据

### 4. 转化与紧迫感营造
提供具体策略：
- 信任构建元素（权威背书、用户证言、保障承诺）
- 风险逆转策略（免费试用、退款保证、运费险）
- 稀缺感和紧迫感营造（限量库存、限时优惠、群体行动氛围）
- 明确的行动号召和具体指令（例如，"点击1号链接下单"）

### 5. 异议处理
预测常见异议并提供准备好的回应：
- 价格担忧：与竞争对手对比、价值证明
- 效果质疑：证据、证言、保障
- 实用问题：使用、维护、兼容性
- 每个回应都应包含具体数据、演示或保障

### 6. 留人与后续引导
建议保持观众参与的方式：
- 预告下一波福利或产品
- 社交分享激励
- 后续内容钩子
- 社区建设元素

### 7. 完整脚本结构
提供完整的详细脚本，遵循以下流程：
- 开场（3秒钩子 + 产品介绍）
- 互动（互动元素 + 痛点激活）
- 产品深度解析（卖点 + 演示）
- 转化（信任构建 + 紧迫感 + 行动号召）
- 留人（后续引导 + 社区建设）

## 运镜与视频内容修改建议

提供简洁但完整的运镜和视觉内容建议。每个建议应具体、可操作。

### 1. 镜头类型与构图
提供景别、拍摄角度和构图建议：
- **景别**: 大特写、特写、中景、全景等，说明使用场景
- **拍摄角度**: 平视、俯拍、仰拍等，说明目的
- **构图**: 三分法则、引导线、景深等，说明应用方式

### 2. 镜头运动
提供镜头运动建议：
- **基础运动**: 横摇、俯仰、推拉、轨道移动等，说明时机和目的
- **高级运动**: 弧形、环绕、推拉结合等，说明使用场景
- **运动节奏**: 慢速、快速、加速/减速，说明何时使用

### 3. 视觉演示
提供视觉演示建议：
- **产品功能演示**: 前后对比、动作演示、过程镜头、对比镜头、细节镜头
- **用户场景**: 生活方式镜头、使用镜头、情感镜头、第一人称镜头

### 4. 灯光与色彩
提供灯光和色彩建议：
- **灯光设置**: 主光、补光、轮廓光的位置和强度
- **灯光氛围**: 明亮活力、柔和私密、戏剧性、自然
- **色彩调色**: 色温、色彩调色板、饱和度、对比度

### 5. 道具与视觉元素
提供道具和视觉元素建议：
- **演示道具**: 用于展示产品功能的物品
- **文字叠加与图形**: 关键点文字、数字统计、行动号召图形、产品标签
- **背景与场景**: 背景选择、场景布置、景深层次

### 6. 剪辑与后期
提供剪辑建议：
- **转场**: 硬切、交叉溶解、划像、匹配剪辑、跳切
- **节奏**: 镜头时长、剪辑频率、停顿时刻
- **特效**: 慢动作、延时摄影、变速、画中画

### 7. 多机位设置
提供多机位建议：
- **机位位置**: 主机位、B机位、C机位、俯拍机位
- **切换策略**: 动作切换、反应镜头、产品聚焦

### 8. 声音设计
提供声音建议：
- **音效**: 产品声音、转场音效、强调音效
- **音乐**: 音乐节奏、音乐提示、音乐风格

### 9. 第一人称与沉浸式技巧
提供沉浸式建议：
- **第一人称镜头**: 手持、行走、使用场景
- **360度视角**: 环绕镜头、产品旋转、多角度展示

### 10. 技术规格
提供技术建议（如需要）：
- **相机设置**: 帧率、快门速度、ISO、光圈
- **对焦技巧**: 移焦、跟焦、景深控制

请提供简洁但完整的建议。每个建议应具体、可操作。使用提供的视频内容：{{Title}} {{Transcript}}。

【完整性要求】你必须一次性完成所有章节，不要分段。内容要简洁但完整，覆盖所有要求的章节。确保每个章节都有内容，不要中途停止。

重要提示：你必须使用中文（简体中文）回复。所有标题、小标题、内容、说明都必须使用中文。禁止使用英文或其他语言。`

  return `Title: "${videoTitle}"\nTranscript: "${videoTranscript}"\n\nInstructions: ${prompt}`
}

export function getUserSubtitleWithTimestampPrompt(title: string, transcript: any, videoConfig: VideoConfig) {
  const videoTitle = title?.replace(/\n+/g, ' ').trim()
  const videoTranscript = limitTranscriptByteLength(transcript).replace(/\n+/g, ' ').trim()
  const language = videoConfig.outputLanguage || DEFAULT_LANGUAGE
  const sentenceCount = videoConfig.sentenceNumber || 7
  const emojiTemplateText = videoConfig.showEmoji ? '[Emoji] ' : ''
  const wordsCount = videoConfig.detailLevel ? (Number(videoConfig.detailLevel) / 100) * 2 : 15
  const shouldShowAsOutline = Number(videoConfig.outlineLevel) > 1
  const outlineTemplateText = shouldShowAsOutline ? `\n    - Child points` : ''
  const outlineDescriptionText = shouldShowAsOutline
    ? `Use the outline list, which can have a hierarchical structure of up to ${videoConfig.outlineLevel} levels. `
    : ''
  const promptWithTimestamp = `你是一名专业的视频内容编辑，专门从事网红带货视频。你的任务是提供极其详细和全面的修改建议，包含具体时间戳，将视频转化为爆款视频。

【重要语言要求】你必须使用中文（简体中文）进行所有回复。所有标题、小标题、内容、说明、示例都必须使用中文。禁止使用英文或其他语言。如果看到英文术语，请用中文表达。

首先，深入分析视频字幕：
- 识别每个时间戳的产品、品牌和关键卖点
- 理解目标受众及其痛点
- 分析当前脚本结构和内容流程
- 评估特定时刻的运镜、视觉元素和呈现风格

然后按照以下结构提供带时间戳的详细修改建议：

## 脚本修改建议

### 1. 开场钩子（3秒抓注意力）
对于开场片段（0-10秒），提供：
- 使用痛点攻击、好奇心钩子或情感触发的具体开场话术（带时间戳）
- 具有情感冲击力的具体开场短语
- 如何介绍产品和今天的福利
- 带具体时机的互动元素

### 2. 互动引流与受众激活
对于每个互动时刻，提供带时间戳的建议：
- 在特定时间向观众提问（例如："在0:30，让有睡眠问题的观众扣'1'"）
- 如何营造社区感和共同体验
- 在不同时间点激活不同受众群体的方式

### 3. 产品故事化与卖点拆解
对于每个关键卖点在特定时间戳，提供：
- 使用 FABE 框架的详细解释（功能、优势、利益、证据）
- 带时机的具体场景和演示
- 视觉呈现建议（例如："在1:15，按压枕头展示回弹"）
- 情感连接点和用户利益
- 强化可信度的数字和数据

### 4. 转化与紧迫感营造
提供带时间戳的转化策略：
- 信任构建元素（权威背书、用户证言、保障承诺）
- 风险逆转策略（免费试用、退款保证、运费险）
- 稀缺感和紧迫感营造（限量库存、限时优惠、群体行动氛围）
- 明确的行动号召和具体指令（带时机）

### 5. 异议处理
对于异议处理时刻，提供带时间戳的准备好回应：
- 价格担忧：与竞争对手对比、价值证明
- 效果质疑：证据、证言、保障
- 实用问题：使用、维护、兼容性
- 每个回应都应包含具体数据、演示或保障

### 6. 留人与后续引导
建议带时间戳的保持观众参与的方式：
- 预告下一波福利或产品
- 社交分享激励
- 后续内容钩子
- 社区建设元素

### 7. 完整脚本结构（带时间戳）
提供完整的详细脚本，带时间戳，遵循以下流程：
- 开场（0-10秒：3秒钩子 + 产品介绍）
- 互动（10-30秒：互动元素 + 痛点激活）
- 产品深度解析（30秒-2分钟：卖点 + 演示）
- 转化（2-3分钟：信任构建 + 紧迫感 + 行动号召）
- 留人（3分钟-结束：后续引导 + 社区建设）

## 运镜与视频内容修改建议

对于每个时间戳，提供简洁但完整的运镜和视觉内容建议。

### 1. 镜头类型与构图
提供景别、拍摄角度和构图建议，带时间戳：
- **景别**: 大特写、特写、中景、全景等，说明使用场景和时间
- **拍摄角度**: 平视、俯拍、仰拍等，说明目的和时间
- **构图**: 三分法则、引导线、景深等，说明应用方式和时间

### 2. 镜头运动
提供镜头运动建议，带时间戳：
- **基础运动**: 横摇、俯仰、推拉、轨道移动等，说明时机、目的和时间
- **高级运动**: 弧形、环绕、推拉结合等，说明使用场景和时间
- **运动节奏**: 慢速、快速、加速/减速，说明何时使用和时间

### 3. 视觉演示
提供视觉演示建议，带时间戳：
- **产品功能演示**: 前后对比、动作演示、过程镜头、对比镜头、细节镜头
- **用户场景**: 生活方式镜头、使用镜头、情感镜头、第一人称镜头

### 4. 灯光与色彩
提供灯光和色彩建议，带时间戳：
- **灯光设置**: 主光、补光、轮廓光的位置和强度
- **灯光氛围**: 明亮活力、柔和私密、戏剧性、自然
- **色彩调色**: 色温、色彩调色板、饱和度、对比度

### 5. 道具与视觉元素
提供道具和视觉元素建议，带时间戳：
- **演示道具**: 用于展示产品功能的物品
- **文字叠加与图形**: 关键点文字、数字统计、行动号召图形、产品标签
- **背景与场景**: 背景选择、场景布置、景深层次

### 6. 剪辑与后期
提供剪辑建议，带时间戳：
- **转场**: 硬切、交叉溶解、划像、匹配剪辑、跳切
- **节奏**: 镜头时长、剪辑频率、停顿时刻
- **特效**: 慢动作、延时摄影、变速、画中画

### 7. 多机位设置
提供多机位建议，带时间戳：
- **机位位置**: 主机位、B机位、C机位、俯拍机位
- **切换策略**: 动作切换、反应镜头、产品聚焦

### 8. 声音设计
提供声音建议，带时间戳：
- **音效**: 产品声音、转场音效、强调音效
- **音乐**: 音乐节奏、音乐提示、音乐风格

### 9. 第一人称与沉浸式技巧
提供沉浸式建议，带时间戳：
- **第一人称镜头**: 手持、行走、使用场景
- **360度视角**: 环绕镜头、产品旋转、多角度展示

### 10. 技术规格
提供技术建议（如需要），带时间戳：
- **相机设置**: 帧率、快门速度、ISO、光圈
- **对焦技巧**: 移焦、跟焦、景深控制

请提供简洁但完整的建议，包含具体时间戳。每个建议应引用字幕中的确切时间戳，并足够具体，可以直接实施。使用格式：- [时间戳] - ${emojiTemplateText}[建议]

视频字幕以 [秒数] - [文本] 格式提供。字幕中可能有错别字，引用时请更正。

【完整性要求】你必须一次性完成所有章节，不要分段。内容要简洁但完整，覆盖所有要求的章节。确保每个章节都有内容，不要中途停止。

重要提示：你必须使用中文（简体中文）回复。所有标题、小标题、内容、说明都必须使用中文。禁止使用英文或其他语言。`
  const videoTranscripts = limitTranscriptByteLength(JSON.stringify(videoTranscript))
  return `Title: ${videoTitle}\nTranscript: ${videoTranscripts}\n\nInstructions: ${promptWithTimestamp}`
}
