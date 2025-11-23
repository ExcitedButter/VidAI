import { zodResolver } from '@hookform/resolvers/zod'
import getVideoId from 'get-video-id'
import type { NextPage } from 'next'
import { useSearchParams } from 'next/navigation'
import { useRouter } from 'next/router'
import React, { useEffect, useState } from 'react'
import { SubmitHandler, useForm } from 'react-hook-form'
import useFormPersist from 'react-hook-form-persist'
import { useAnalytics } from '~/components/context/analytics'
import { SubmitButton } from '~/components/SubmitButton'
import { SummaryResult } from '~/components/SummaryResult'
import { TypingSlogan } from '~/components/TypingSlogan'
import { UsageAction } from '~/components/UsageAction'
import { useToast } from '~/hooks/use-toast'
import { useLocalStorage } from '~/hooks/useLocalStorage'
import { useSummarize } from '~/hooks/useSummarize'
import { VideoService } from '~/lib/types'
import { DEFAULT_LANGUAGE } from '~/utils/constants/language'
import { extractPage, extractUrl } from '~/utils/extractUrl'
import { extractXiaohongshuUrl } from '~/utils/extractXiaohongshuUrl'
import { getVideoIdFromUrl } from '~/utils/getVideoIdFromUrl'
import { VideoConfigSchema, videoConfigSchema } from '~/utils/schemas/video'

export const Home: NextPage<{
  showSingIn: (show: boolean) => void
}> = ({ showSingIn }) => {
  const router = useRouter()
  const urlState = router.query.slug
  const searchParams = useSearchParams()
  const licenseKey = searchParams.get('license_key')

  const {
    register,
    handleSubmit,
    control,
    trigger,
    getValues,
    watch,
    setValue,
    formState: { errors },
  } = useForm<VideoConfigSchema>({
    defaultValues: {
      enableStream: true,
      showTimestamp: false,
      showEmoji: true,
      detailLevel: 600,
      sentenceNumber: 5,
      outlineLevel: 1,
      outputLanguage: DEFAULT_LANGUAGE,
    },
    resolver: zodResolver(videoConfigSchema),
  })

  // TODO: add mobx or state manager
  const [currentVideoId, setCurrentVideoId] = useState<string>('')
  const [currentVideoUrl, setCurrentVideoUrl] = useState<string>('')
  const [userKey, setUserKey] = useLocalStorage<string>('user-openai-apikey')
  const { loading, summary, resetSummary, summarize } = useSummarize(showSingIn, getValues('enableStream'))
  const { toast } = useToast()
  const { analytics } = useAnalytics()

  useFormPersist('video-summary-config-storage', {
    watch,
    setValue,
    storage: typeof window !== 'undefined' ? window.localStorage : undefined, // default window.sessionStorage
    // exclude: ['baz']
  })
  const shouldShowTimestamp = getValues('showTimestamp')

  useEffect(() => {
    licenseKey && setUserKey(licenseKey)
  }, [licenseKey])

  useEffect(() => {
    // https://www.youtube.com/watch?v=DHhOgWPKIKU
    // todo: support redirect from www.youtube.jimmylv.cn/watch?v=DHhOgWPKIKU
    const validatedUrl = getVideoIdFromUrl(router.isReady, currentVideoUrl, urlState, searchParams)

    console.log('getVideoUrlFromUrl', validatedUrl)

    validatedUrl && generateSummary(validatedUrl)
  }, [router.isReady, urlState, searchParams])

  const validateUrlFromAddressBar = (url?: string) => {
    // note: auto refactor by ChatGPT
    const videoUrl = url || currentVideoUrl
    if (
      // https://www.bilibili.com/video/BV1AL4y1j7RY
      // https://www.bilibili.com/video/BV1854y1u7B8/?p=6
      // https://www.bilibili.com/video/av352747000
      // https://www.xiaohongshu.com/explore/xxxxx
      // https://xhslink.com/xxxxx
      // todo: b23.tv url with title
      // todo: any article url
      !(
        videoUrl.includes('bilibili.com/video') ||
        videoUrl.includes('youtube.com') ||
        videoUrl.includes('xiaohongshu.com') ||
        videoUrl.includes('xhslink.com')
      )
    ) {
      toast({
        title: '暂不支持此视频链接',
        description: '请输入哔哩哔哩、YouTube 或小红书视频链接，已支持 b23.tv 和 xhslink.com 短链接',
      })
      return false
    }

    // 来自输入框
    if (!url) {
      // 小红书链接不需要路由替换，直接使用完整 URL
      if (videoUrl.includes('xiaohongshu.com') || videoUrl.includes('xhslink.com')) {
        setCurrentVideoUrl(videoUrl)
      } else {
        // -> '/video/BV12Y4y127rj'
        const curUrl = String(videoUrl.split('.com')[1])
        router.replace(curUrl)
      }
    } else {
      setCurrentVideoUrl(videoUrl)
    }
    return true
  }
  const generateSummary = async (url?: string) => {
    const formValues = getValues()
    console.log('=======formValues=========', formValues)

    resetSummary()
    const isValid = validateUrlFromAddressBar(url)
    if (!isValid) {
      return
    }

    const videoUrl = url || currentVideoUrl
    console.log('Processing video URL:', videoUrl)
    
    const { id, service } = getVideoId(videoUrl)
    if (service === VideoService.Youtube && id) {
      console.log('Processing YouTube video:', id)
      setCurrentVideoId(id)
      await summarize(
        { videoId: id, service: VideoService.Youtube, ...formValues },
        { userKey, shouldShowTimestamp: shouldShowTimestamp },
      )
      return
    }

    // 检查是否是小红书链接（优先检查，因为小红书链接可能包含其他关键词）
    const xiaohongshuId = extractXiaohongshuUrl(videoUrl)
    if (xiaohongshuId) {
      console.log('Processing Xiaohongshu video:', xiaohongshuId)
      setCurrentVideoId(xiaohongshuId)
      await summarize(
        { service: VideoService.Xiaohongshu, videoId: xiaohongshuId, ...formValues },
        { userKey, shouldShowTimestamp },
      )
      return
    }

    // 如果不是小红书链接，尝试提取 Bilibili 视频 ID
    const videoId = extractUrl(videoUrl)
    if (!videoId) {
      console.error('Failed to extract video ID from URL:', videoUrl)
      console.log('URL contains xiaohongshu:', videoUrl.includes('xiaohongshu'))
      console.log('URL contains xhslink:', videoUrl.includes('xhslink'))
      
      // 如果包含小红书相关关键词但提取失败，给出更具体的提示
      if (videoUrl.includes('xiaohongshu') || videoUrl.includes('xhslink')) {
        toast({
          title: '无法识别小红书视频链接',
          description: '请确保链接格式为：https://www.xiaohongshu.com/explore/xxxxx 或 https://xhslink.com/xxxxx',
        })
      } else {
        toast({
          title: '无法识别视频链接',
          description: '请检查视频链接格式是否正确。支持：Bilibili、YouTube、小红书',
        })
      }
      return
    }

    console.log('Processing Bilibili video:', videoId)
    const pageNumber = extractPage(currentVideoUrl, searchParams)
    setCurrentVideoId(videoId)
    await summarize(
      { service: VideoService.Bilibili, videoId, pageNumber, ...formValues },
      { userKey, shouldShowTimestamp },
    )
    setTimeout(() => {
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })
    }, 10)
  }
  const onFormSubmit: SubmitHandler<VideoConfigSchema> = async (data) => {
    // e.preventDefault();
    await generateSummary(currentVideoUrl)
    analytics.track('GenerateButton Clicked')
  }
  const handleApiKeyChange = (e: any) => {
    setUserKey(e.target.value)
  }

  const handleInputChange = async (e: any) => {
    const value = e.target.value
    // todo: 兼容?query参数
    const regex = /((?:https?:\/\/|www\.)\S+)/g
    const matches = value.match(regex)
    if (matches && matches[0].includes('b23.tv')) {
      toast({ title: '正在自动转换此视频链接...' })
      const response = await fetch(`/api/b23tv?url=${matches[0]}`)
      const json = await response.json()
      setCurrentVideoUrl(json.url)
    } else if (matches && matches[0].includes('xhslink.com')) {
      // 小红书短链接需要解析，但通常可以直接使用
      // 如果需要解析，可以在这里添加 API 调用
      setCurrentVideoUrl(value)
    } else {
      setCurrentVideoUrl(value)
    }
  }

  return (
    <div className="mt-10 w-full px-4 sm:mt-20 lg:px-0">
      <TypingSlogan />
      <UsageAction />
      <form onSubmit={handleSubmit(onFormSubmit)} className="mx-auto mt-8 flex max-w-3xl flex-col items-center">
        <div className="relative w-full">
          <input
            type="text"
            value={currentVideoUrl}
            onChange={handleInputChange}
            className="w-full appearance-none rounded-2xl border border-gray-300 bg-white px-4 py-4 text-base leading-6 text-gray-900 shadow-sm transition-all placeholder:text-gray-400 focus:border-gray-400 focus:outline-none focus:ring-0 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder:text-gray-500 dark:focus:border-gray-500"
            placeholder={'输入 bilibili.com/youtube.com/xiaohongshu.com 视频链接，按下「回车」'}
          />
        </div>
        <SubmitButton loading={loading} />
      </form>
      {summary && (
        <SummaryResult
          summary={summary}
          currentVideoUrl={currentVideoUrl}
          currentVideoId={currentVideoId}
          shouldShowTimestamp={shouldShowTimestamp}
        />
      )}
    </div>
  )
}

export default Home
