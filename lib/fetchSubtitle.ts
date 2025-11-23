import { fetchBilibiliSubtitle } from './bilibili/fetchBilibiliSubtitle'
import { CommonSubtitleItem, VideoConfig, VideoService } from './types'
import { fetchYoutubeSubtitle } from './youtube/fetchYoutubeSubtitle'
import { fetchXiaohongshuSubtitle } from './xiaohongshu/fetchXiaohongshuSubtitle'

export async function fetchSubtitle(
  videoConfig: VideoConfig,
  shouldShowTimestamp?: boolean,
): Promise<{
  title: string
  subtitlesArray?: null | Array<CommonSubtitleItem>
  descriptionText?: string
}> {
  const { service, videoId, pageNumber } = videoConfig
  console.log('video: ', videoConfig)
  if (service === VideoService.Youtube) {
    return await fetchYoutubeSubtitle(videoId, shouldShowTimestamp)
  }
  if (service === VideoService.Xiaohongshu) {
    return await fetchXiaohongshuSubtitle(videoId, shouldShowTimestamp)
  }
  return await fetchBilibiliSubtitle(videoId, pageNumber, shouldShowTimestamp)
}
