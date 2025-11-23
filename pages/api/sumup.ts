import type { NextFetchEvent, NextRequest } from 'next/server'
import { NextResponse } from 'next/server'
import { fetchSubtitle } from '~/lib/fetchSubtitle'
import { ChatGPTAgent, fetchOpenAIResult } from '~/lib/openai/fetchOpenAIResult'
import { getSmallSizeTranscripts } from '~/lib/openai/getSmallSizeTranscripts'
import { getUserSubtitlePrompt, getUserSubtitleWithTimestampPrompt } from '~/lib/openai/prompt'
import { selectApiKeyAndActivatedLicenseKey } from '~/lib/openai/selectApiKeyAndActivatedLicenseKey'
import { SummarizeParams } from '~/lib/types'
import { isDev } from '~/utils/env'

export const config = {
  runtime: 'edge',
}

if (!process.env.OPENAI_API_KEY) {
  throw new Error('Missing env var from OpenAI')
}

export default async function handler(req: NextRequest, context: NextFetchEvent) {
  const { videoConfig, userConfig } = (await req.json()) as SummarizeParams
  const { userKey, shouldShowTimestamp } = userConfig
  const { videoId } = videoConfig

  if (!videoId) {
    return new Response('No videoId in the request', { status: 500 })
  }
  let title, subtitlesArray, descriptionText
  try {
    const result = await fetchSubtitle(videoConfig, shouldShowTimestamp)
    title = result.title
    subtitlesArray = result.subtitlesArray
    descriptionText = result.descriptionText
  } catch (error: any) {
    console.error('Error fetching subtitle:', error)
    return new Response(
      JSON.stringify({
        errorMessage: `获取视频内容失败: ${error.message || '未知错误'}`,
      }),
      {
        status: 500,
      },
    )
  }
  
  if (!subtitlesArray && !descriptionText) {
    console.error('No subtitle or description in the video: ', videoId)
    return new Response(
      JSON.stringify({
        errorMessage: '该视频没有字幕或描述内容，无法生成摘要',
      }),
      {
        status: 501,
      },
    )
  }
  const inputText = subtitlesArray ? getSmallSizeTranscripts(subtitlesArray, subtitlesArray) : descriptionText // subtitlesArray.map((i) => i.text).join("\n")

  // TODO: try the apiKey way for chrome extensions
  // const systemPrompt = getSystemPrompt({
  //   shouldShowTimestamp: subtitlesArray ? shouldShowTimestamp : false,
  // });
  // const examplePrompt = getExamplePrompt();
  const userPrompt = shouldShowTimestamp
    ? getUserSubtitleWithTimestampPrompt(title, inputText, videoConfig)
    : getUserSubtitlePrompt(title, inputText, videoConfig)
  if (isDev) {
    // console.log("final system prompt: ", systemPrompt);
    // console.log("final example prompt: ", examplePrompt);
    console.log('final user prompt: ', userPrompt)
  }

  try {
    const stream = true
    // Increase max_tokens to prevent premature truncation
    // For detailed video editing suggestions, we need significantly more tokens
    // GPT-3.5-turbo has a max of 4096 tokens, we use 4000 to leave room for input
    // If detailLevel is provided (typically 300-1000), multiply it by 4 to ensure enough tokens
    // Otherwise use a high default value (3000-3200) to ensure complete output
    const detailLevel = Number(videoConfig.detailLevel)
    const calculatedTokens = detailLevel ? detailLevel * 4 : (userKey ? 3200 : 3000)
    const openAiPayload = {
      model: 'gpt-3.5-turbo',
      messages: [
        // { role: ChatGPTAgent.system, content: systemPrompt },
        // { role: ChatGPTAgent.user, content: examplePrompt.input },
        // { role: ChatGPTAgent.assistant, content: examplePrompt.output },
        { role: ChatGPTAgent.user, content: userPrompt },
      ],
      // temperature: 0.5,
      // top_p: 1,
      // frequency_penalty: 0,
      // presence_penalty: 0,
      max_tokens: Math.min(Math.max(calculatedTokens, 3000), 4000),
      stream,
      // n: 1,
    }

    // TODO: need refactor
    const openaiApiKey = await selectApiKeyAndActivatedLicenseKey(userKey, videoId)
    const result = await fetchOpenAIResult(openAiPayload, openaiApiKey, videoConfig)
    if (stream) {
      return new Response(result)
    }

    return NextResponse.json(result)
  } catch (error: any) {
    console.error(error.message)
    return new Response(
      JSON.stringify({
        errorMessage: error.message,
      }),
      {
        status: 500,
      },
    )
  }
}
