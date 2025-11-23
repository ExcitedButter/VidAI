/**
 * 提取小红书视频 ID
 * 支持格式：
 * - https://www.xiaohongshu.com/explore/xxxxx
 * - https://www.xiaohongshu.com/discovery/item/xxxxx
 * - https://www.xiaohongshu.com/search_result/xxxxx (搜索结果格式)
 * - https://xhslink.com/xxxxx (短链接)
 * - https://www.xiaohongshu.com/user/profile/xxxxx/xxxxx (用户主页的视频)
 */
export function extractXiaohongshuUrl(videoUrl: string): string | null {
  console.log('Extracting xiaohongshu URL from:', videoUrl)
  
  // 处理 xiaohongshu.com/explore/ 格式
  let xhsMatch = videoUrl.match(/xiaohongshu\.com\/explore\/([^\/\?&#]+)/)
  if (xhsMatch) {
    console.log('Matched explore format:', xhsMatch[1])
    return xhsMatch[1]
  }

  // 处理 xiaohongshu.com/discovery/item/ 格式
  xhsMatch = videoUrl.match(/xiaohongshu\.com\/discovery\/item\/([^\/\?&#]+)/)
  if (xhsMatch) {
    console.log('Matched discovery/item format:', xhsMatch[1])
    return xhsMatch[1]
  }

  // 处理 xiaohongshu.com/search_result/ 格式
  xhsMatch = videoUrl.match(/xiaohongshu\.com\/search_result\/([^\/\?&#]+)/)
  if (xhsMatch) {
    console.log('Matched search_result format:', xhsMatch[1])
    return xhsMatch[1]
  }

  // 处理 xhslink.com 短链接
  const xhslinkMatch = videoUrl.match(/xhslink\.com\/([^\/\?&#]+)/)
  if (xhslinkMatch) {
    console.log('Matched xhslink format:', xhslinkMatch[1])
    // 短链接需要先解析，这里返回短链接 ID
    return xhslinkMatch[1]
  }

  // 处理其他可能的格式
  // 例如：https://www.xiaohongshu.com/user/profile/xxxxx/xxxxx
  xhsMatch = videoUrl.match(/xiaohongshu\.com\/[^\/]+\/([a-zA-Z0-9]+)(?:\?|$|#)/)
  if (xhsMatch && !videoUrl.includes('/explore/') && !videoUrl.includes('/discovery/') && !videoUrl.includes('/search_result/')) {
    console.log('Matched other format:', xhsMatch[1])
    return xhsMatch[1]
  }

  console.log('No match found for xiaohongshu URL')
  return null
}

