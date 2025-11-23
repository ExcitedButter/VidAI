/**
 * 获取小红书视频信息
 * 参考 bilibili 的处理方式，直接调用 API 获取数据
 */
interface XiaohongshuVideoInfo {
  title: string
  desc?: string
  note?: {
    desc?: string
    note?: {
      desc?: string
    }
  }
}

export const fetchXiaohongshuSubtitleUrls = async (
  videoId: string,
): Promise<XiaohongshuVideoInfo> => {
  // 参考 bilibili 的方式，直接调用我们的 API 路由（Node.js runtime）
  // 我们的 API 路由已经集成了 KuKuTool 服务
  // 参考: https://dy.kukutool.com/
  const apiUrl = typeof window !== 'undefined' 
    ? '/api/xiaohongshu'
    : `${process.env.NEXT_PUBLIC_BASE_URL || process.env.VERCEL_URL 
        ? `https://${process.env.VERCEL_URL}` 
        : 'http://localhost:3000'}/api/xiaohongshu`
  
  console.log(`fetch xiaohongshu via API route (with KuKuTool)`, apiUrl)
  
  const commonConfig: RequestInit = {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({ videoId }),
    cache: 'no-cache',
  }

  try {
    const response = await fetch(apiUrl, commonConfig)
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.message || `API request failed: ${response.status} ${response.statusText}`)
    }
    
    const json = await response.json()
    console.log('API response:', json)
    
    // 返回格式化的数据（参考 bilibili 的格式）
    return {
      title: json.title || '小红书视频',
      desc: json.descriptionText,
      note: {
        desc: json.descriptionText,
        note: {
          desc: json.descriptionText,
        },
      },
    }
  } catch (error: any) {
    console.error('Error fetching xiaohongshu video:', error)
    throw error
  }
}

