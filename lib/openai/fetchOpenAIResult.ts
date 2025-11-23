import { Redis } from '@upstash/redis'
import { createParser, ParsedEvent, ReconnectInterval } from 'eventsource-parser'
import { trimOpenAiResult } from '~/lib/openai/trimOpenAiResult'
import { VideoConfig } from '~/lib/types'
import { isDev } from '~/utils/env'
import { getCacheId } from '~/utils/getCacheId'

export enum ChatGPTAgent {
  user = 'user',
  system = 'system',
  assistant = 'assistant',
}

export interface ChatGPTMessage {
  role: ChatGPTAgent
  content: string
}
export interface OpenAIStreamPayload {
  api_key?: string
  model: string
  messages: ChatGPTMessage[]
  temperature?: number
  top_p?: number
  frequency_penalty?: number
  presence_penalty?: number
  max_tokens: number
  stream: boolean
  n?: number
}

export async function fetchOpenAIResult(payload: OpenAIStreamPayload, apiKey: string, videoConfig: VideoConfig) {
  const encoder = new TextEncoder()
  const decoder = new TextDecoder()

  isDev && console.log({ apiKey })
  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${apiKey ?? ''}`,
    },
    method: 'POST',
    body: JSON.stringify(payload),
  })

  if (res.status !== 200) {
    const errorJson = await res.json()
    throw new Error(`OpenAI API Error [${res.statusText}]: ${errorJson.error?.message}`)
  }

  const redis = Redis.fromEnv()
  const cacheId = getCacheId(videoConfig)

  if (!payload.stream) {
    const result = await res.json()
    const betterResult = trimOpenAiResult(result)

    const data = await redis.set(cacheId, betterResult)
    console.info(`video ${cacheId} cached:`, data)
    isDev && console.log('========betterResult========', betterResult)

    return betterResult
  }

  let counter = 0
  let tempData = ''
  const stream = new ReadableStream({
    async start(controller) {
      // callback
      async function onParse(event: ParsedEvent | ReconnectInterval) {
        if (event.type === 'event') {
          const data = event.data
          // https://beta.openai.com/docs/api-reference/completions/create#completions/create-stream
          if (data === '[DONE]') {
            // active
            controller.close()
            const data = await redis.set(cacheId, tempData)
            console.info(`video ${cacheId} cached:`, data)
            isDev && console.log('========betterResult after streamed========', tempData)
            return
          }
          try {
            const json = JSON.parse(data)
            const text = json.choices[0].delta?.content || ''
            // Check if the response was cut off due to finish_reason
            if (json.choices?.[0]?.finish_reason === 'length') {
              console.warn('⚠️ OpenAI response was truncated due to max_tokens limit')
              // Add a warning message to the stream
              const warningText = '\n\n⚠️ [注意：由于内容较长，生成可能被截断。建议增加详细程度设置以获得完整内容。]'
              tempData += warningText
              const warningQueue = encoder.encode(warningText)
              controller.enqueue(warningQueue)
            }
            // todo: add redis cache
            tempData += text
            if (counter < 2 && (text.match(/\n/) || []).length) {
              // this is a prefix character (i.e., "\n\n"), do nothing
              return
            }
            const queue = encoder.encode(text)
            controller.enqueue(queue)
            counter++
          } catch (e) {
            // Log parse error but don't stop the stream
            console.error('Error parsing stream data:', e, 'Data:', data)
            // Try to continue instead of stopping
            // controller.error(e)
          }
        }
      }

      // stream response (SSE) from OpenAI may be fragmented into multiple chunks
      // this ensures we properly read chunks and invoke an event for each SSE event stream
      const parser = createParser(onParse)
      // https://web.dev/streams/#asynchronous-iteration
      try {
        for await (const chunk of res.body as any) {
          parser.feed(decoder.decode(chunk))
        }
      } catch (streamError: any) {
        console.error('Stream reading error:', streamError)
        // If stream was interrupted, try to close gracefully
        if (tempData.length > 0) {
          // Send what we have so far
          controller.enqueue(encoder.encode('\n\n[生成中断，但已保存部分内容]'))
        }
        controller.error(streamError)
      }
    },
  })

  return stream
}
