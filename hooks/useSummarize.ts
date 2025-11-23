import { useState } from 'react'
import { useToast } from '~/hooks/use-toast'
import { UserConfig, VideoConfig } from '~/lib/types'
import { RATE_LIMIT_COUNT } from '~/utils/constants'

export function useSummarize(showSingIn: (show: boolean) => void, enableStream: boolean = true) {
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<string>('')
  const { toast } = useToast()

  const resetSummary = () => {
    setSummary('')
  }

  const summarize = async (videoConfig: VideoConfig, userConfig: UserConfig) => {
    setSummary('')
    setLoading(true)

    try {
      setLoading(true)
      const response = await fetch('/api/sumup', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          videoConfig,
          userConfig,
        }),
      })

      if (response.redirected) {
        window.location.href = response.url
      }

      if (!response.ok) {
        console.log('error', response)
        if (response.status === 501) {
          toast({
            title: '啊叻？视频字幕不见了？！',
            description: `\n（这个视频太短了...\n或者还没有字幕哦！）`,
          })
        } else if (response.status === 504) {
          toast({
            variant: 'destructive',
            title: `网站访问量过大`,
            description: `每日限额使用 ${RATE_LIMIT_COUNT} 次哦！`,
          })
        } else if (response.status === 401) {
          toast({
            variant: 'destructive',
            title: `${response.statusText} 请登录哦！`,
            // ReadableStream can't get error message
            // description: response.body
            description: '每天的免费次数已经用完啦，🆓',
          })
          showSingIn(true)
        } else {
          const errorJson = await response.json()
          toast({
            variant: 'destructive',
            title: response.status + ' ' + response.statusText,
            // ReadableStream can't get error message
            description: errorJson.errorMessage,
          })
        }
        setLoading(false)
        return
      }

      if (enableStream) {
        // This data is a ReadableStream
        const data = response.body
        if (!data) {
          setLoading(false)
          toast({
            variant: 'destructive',
            title: '流式响应错误',
            description: '无法获取响应流，请重试',
          })
          return
        }

        const reader = data.getReader()
        const decoder = new TextDecoder()
        let done = false

        let accumulatedText = ''
        try {
          while (!done) {
            const { value, done: doneReading } = await reader.read()
            done = doneReading
            
            if (value) {
              const chunkValue = decoder.decode(value, { stream: true })
              accumulatedText += chunkValue
              setSummary((prev) => prev + chunkValue)
              
              // Check for truncation warning in the accumulated text
              if (accumulatedText.includes('finish_reason":"length"') || accumulatedText.includes('被截断')) {
                console.warn('检测到内容被截断')
                toast({
                  variant: 'default',
                  title: '内容较长',
                  description: '生成的内容可能较长，如果看到截断提示，建议增加详细程度设置以获得完整内容。',
                })
              }
            }
          }
          setLoading(false)
        } catch (streamError: any) {
          console.error('Stream reading error:', streamError)
          setLoading(false)
          toast({
            variant: 'destructive',
            title: '生成中断',
            description: streamError.message || '流式生成过程中发生错误，但已保存部分内容',
          })
        }
        return
      }
      // await readStream(response, setSummary);
      const result = await response.json()
      if (result.errorMessage) {
        setLoading(false)
        toast({
          variant: 'destructive',
          title: 'API 请求出错，请重试。',
          description: result.errorMessage,
        })
        return
      }
      setSummary(result)
      setLoading(false)
    } catch (e: any) {
      console.error('[fetch ERROR]', e)
      toast({
        variant: 'destructive',
        title: '未知错误：',
        description: e.message || e.errorMessage,
      })
      setLoading(false)
    }
  }
  return { loading, summary, resetSummary, summarize }
}
