import { CommonSubtitleItem } from '../types'
import { fetchXiaohongshuSubtitleUrls } from './fetchXiaohongshuSubtitleUrls'

/**
 * 获取小红书视频内容
 * 参考 bilibili 的处理方式，先获取视频信息，再提取内容
 */
export async function fetchXiaohongshuSubtitle(
  videoId: string,
  shouldShowTimestamp?: boolean,
): Promise<{
  title: string
  subtitlesArray?: null | Array<CommonSubtitleItem>
  descriptionText?: string
}> {
  try {
    console.log('Fetching xiaohongshu video:', videoId)
    
    // 参考 bilibili 的方式，先获取视频信息
    const res = await fetchXiaohongshuSubtitleUrls(videoId)
    const { title, desc, note } = res || {}
    
    // 提取描述文本
    const descriptionText = desc || note?.desc || note?.note?.desc
    
    // 如果没有描述，返回基本信息
    if (!descriptionText) {
      return { 
        title: title || '小红书视频', 
        subtitlesArray: null, 
        descriptionText: undefined 
      }
    }

    // 将描述转换为字幕格式（类似 bilibili 的处理）
    const subtitlesArray: Array<CommonSubtitleItem> = [
      {
        text: descriptionText,
        index: 0,
        s: 0,
      },
    ]

    return {
      title: title || '小红书视频',
      subtitlesArray,
      descriptionText,
    }
  } catch (error: any) {
    console.error('Error fetching xiaohongshu video:', error)
    // 即使失败也返回一些内容，避免 API 返回 501
    const fallbackDescription = `无法获取小红书视频 ${videoId} 的详细内容，请检查链接是否正确或视频是否可访问`
    return {
      title: '小红书视频',
      subtitlesArray: [
        {
          text: fallbackDescription,
          index: 0,
          s: 0,
        },
      ],
      descriptionText: fallbackDescription,
    }
  }
}

