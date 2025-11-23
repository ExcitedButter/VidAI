import type { NextApiRequest, NextApiResponse } from 'next'

/**
 * 小红书视频内容获取 API
 * 使用 Node.js runtime 以避免 Edge Runtime 的限制
 */
export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' })
  }

  const { videoId } = req.body

  if (!videoId) {
    return res.status(400).json({ error: 'videoId is required' })
  }

  try {
    console.log('Fetching xiaohongshu video via API:', videoId)
    
    // 构建小红书视频页面 URL
    // 支持多种格式：explore、discovery/item 等
    let videoUrl = `https://www.xiaohongshu.com/explore/${videoId}`
    
    // 如果 videoId 看起来像是短链接，可能需要先解析
    if (videoId.includes('xhslink') || videoId.length < 20) {
      // 可能是短链接，尝试不同的 URL 格式
      videoUrl = `https://www.xiaohongshu.com/discovery/item/${videoId}`
    }
    
    console.log('Video URL:', videoUrl)
    
    // 方法1: 使用 KuKuTool 网页服务解析
    // 参考: https://dy.kukutool.com/
    // 注意：KuKuTool 可能需要通过实际的网页交互，这里尝试直接调用可能的 API
    try {
      console.log('Trying KuKuTool web service...')
      
      // 尝试多种可能的 KuKuTool API 端点
      // 根据搜索结果，KuKuTool 的 API 应该是 POST 请求到 /api/xiaohongshu
      const kukutoolEndpoints = [
        {
          url: 'https://dy.kukutool.com/api/xiaohongshu',
          method: 'POST' as const,
          body: JSON.stringify({ url: videoUrl }),
        },
        {
          url: `https://dy.kukutool.com/api/xiaohongshu?url=${encodeURIComponent(videoUrl)}`,
          method: 'GET' as const,
        },
        {
          url: 'https://dy.kukutool.com/xiaohongshu/api',
          method: 'POST' as const,
          body: JSON.stringify({ url: videoUrl }),
        },
        // 尝试使用表单格式
        {
          url: 'https://dy.kukutool.com/api/xiaohongshu',
          method: 'POST' as const,
          body: new URLSearchParams({ url: videoUrl }).toString(),
          contentType: 'application/x-www-form-urlencoded',
        },
      ]
      
      for (const endpoint of kukutoolEndpoints) {
        try {
          console.log(`Trying KuKuTool endpoint: ${endpoint.url} (${endpoint.method})`)
          
          const headers: Record<string, string> = {
            'User-Agent':
              'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://dy.kukutool.com/',
            'Origin': 'https://dy.kukutool.com',
            'Accept': 'application/json',
          }
          
          // 根据端点设置 Content-Type
          if (endpoint.contentType) {
            headers['Content-Type'] = endpoint.contentType
          } else {
            headers['Content-Type'] = 'application/json'
          }
          
          const kukutoolResponse = await fetch(endpoint.url, {
            method: endpoint.method,
            headers,
            body: endpoint.body as string | undefined,
          })
          
          console.log(`KuKuTool response status: ${kukutoolResponse.status} ${kukutoolResponse.statusText}`)
          
          if (kukutoolResponse.ok) {
            const contentType = kukutoolResponse.headers.get('content-type') || ''
            
            if (contentType.includes('application/json')) {
              const apiData = await kukutoolResponse.json()
              console.log('KuKuTool API JSON response:', apiData)
              
              const data = apiData.data || apiData
              const title = data.title || data.desc?.substring(0, 50) || '小红书视频'
              const desc = data.desc || data.description || data.note?.desc || data.content || data.text
              
              if (desc && desc.length > 5) {
                console.log('Successfully extracted from KuKuTool API')
                return res.status(200).json({
                  title,
                  descriptionText: desc,
                  subtitlesArray: [
                    {
                      text: desc,
                      index: 0,
                      s: 0,
                    },
                  ],
                })
              }
            } else {
              // HTML 响应，尝试解析
              const kukutoolHtml = await kukutoolResponse.text()
              console.log('KuKuTool HTML response length:', kukutoolHtml.length)
              
              // 尝试从 HTML 中提取 JSON 数据
              const jsonPatterns = [
                /window\.__INITIAL_DATA__\s*=\s*({[\s\S]*?});/i,
                /var\s+result\s*=\s*({[\s\S]*?});/i,
                /const\s+data\s*=\s*({[\s\S]*?});/i,
              ]
              
              for (const pattern of jsonPatterns) {
                const match = kukutoolHtml.match(pattern)
                if (match) {
                  try {
                    const data = JSON.parse(match[1])
                    const title = data.title || data.data?.title || '小红书视频'
                    const desc = data.desc || data.description || data.data?.desc || data.data?.description || data.text
                    
                    if (desc && desc.length > 5) {
                      return res.status(200).json({
                        title,
                        descriptionText: desc,
                        subtitlesArray: [
                          {
                            text: desc,
                            index: 0,
                            s: 0,
                          },
                        ],
                      })
                    }
                  } catch (e) {
                    console.log('Failed to parse KuKuTool JSON from HTML:', e)
                  }
                }
              }
            }
          } else {
            // 记录非 200 响应的详细信息
            const errorText = await kukutoolResponse.text().catch(() => '无法读取响应')
            console.log(`KuKuTool endpoint ${endpoint.url} returned ${kukutoolResponse.status}:`, errorText.substring(0, 200))
          }
        } catch (endpointError: any) {
          console.log(`KuKuTool endpoint ${endpoint.url} failed:`, endpointError.message)
          continue
        }
      }
    } catch (kukutoolError: any) {
      console.log('KuKuTool web service failed:', kukutoolError.message)
    }
    
    // 方法2: 直接访问小红书页面（备用方案）
    // 注意：小红书有反爬虫机制，可能需要 Cookie 或其他认证
    console.log('Trying direct xiaohongshu page access...')
    
    // 尝试多种 URL 格式
    const xiaohongshuUrls = [
      videoUrl, // explore 格式（默认）
      `https://www.xiaohongshu.com/discovery/item/${videoId}`, // discovery/item 格式
      `https://www.xiaohongshu.com/search_result/${videoId}`, // search_result 格式（搜索结果链接）
    ]
    
    let xhsResponse: Response | null = null
    let xhsHtml = ''
    
    for (const url of xiaohongshuUrls) {
      try {
        console.log(`Trying xiaohongshu URL: ${url}`)
        xhsResponse = await fetch(url, {
          headers: {
            'User-Agent':
              'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.xiaohongshu.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
          },
          redirect: 'follow',
        })
        
        console.log(`Response status for ${url}: ${xhsResponse.status} ${xhsResponse.statusText}`)
        
        if (xhsResponse.ok) {
          xhsHtml = await xhsResponse.text()
          // 检查是否是错误页面
          if (!xhsHtml.includes('你访问的页面不见了') && !xhsHtml.includes('页面不存在')) {
            console.log(`Successfully fetched from ${url}`)
            break
          } else {
            console.log(`URL ${url} returned error page`)
            xhsResponse = null
          }
        }
      } catch (urlError: any) {
        console.log(`Failed to fetch ${url}:`, urlError.message)
        continue
      }
    }
    
    if (!xhsResponse) {
      // 如果所有 URL 都失败，返回基本内容
      return res.status(200).json({
        title: '小红书视频',
        descriptionText: `这是来自小红书视频 ${videoId} 的内容。由于无法访问原页面，建议直接访问原视频查看完整内容。`,
        subtitlesArray: [
          {
            text: `这是来自小红书视频 ${videoId} 的内容。由于无法访问原页面，建议直接访问原视频查看完整内容。`,
            index: 0,
            s: 0,
          },
        ],
      })
    }

    console.log('Response status:', xhsResponse.status, xhsResponse.statusText)

    if (!xhsResponse.ok) {
      // 如果返回 404，返回基本内容让流程继续，而不是完全失败
      if (xhsResponse.status === 404) {
        console.log('Xiaohongshu page returned 404, returning fallback content')
        return res.status(200).json({
          title: '小红书视频',
          descriptionText: `这是来自小红书视频 ${videoId} 的内容。该页面可能不存在或已被删除，建议检查链接是否正确。`,
          subtitlesArray: [
            {
              text: `这是来自小红书视频 ${videoId} 的内容。该页面可能不存在或已被删除，建议检查链接是否正确。`,
              index: 0,
              s: 0,
            },
          ],
        })
      }
      // 其他错误也返回基本内容
      console.log(`Xiaohongshu page returned ${xhsResponse.status}, returning fallback content`)
      return res.status(200).json({
        title: '小红书视频',
        descriptionText: `这是来自小红书视频 ${videoId} 的内容。由于无法获取详细描述（错误: ${xhsResponse.status}），建议直接访问原视频查看完整内容。`,
        subtitlesArray: [
          {
            text: `这是来自小红书视频 ${videoId} 的内容。由于无法获取详细描述（错误: ${xhsResponse.status}），建议直接访问原视频查看完整内容。`,
            index: 0,
            s: 0,
          },
        ],
      })
    }

    // 如果之前已经获取了 HTML，使用它；否则重新获取
    if (!xhsHtml) {
      xhsHtml = await xhsResponse.text()
    }
    console.log('HTML length:', xhsHtml.length)
    
    if (xhsHtml.length < 1000) {
      // HTML 太短，可能是错误页面，但返回基本内容让流程继续
      console.log('HTML too short, returning fallback content')
      return res.status(200).json({
        title: '小红书视频',
        descriptionText: `这是来自小红书视频 ${videoId} 的内容。由于无法获取详细描述，建议直接访问原视频查看完整内容。`,
        subtitlesArray: [
          {
            text: `这是来自小红书视频 ${videoId} 的内容。由于无法获取详细描述，建议直接访问原视频查看完整内容。`,
            index: 0,
            s: 0,
          },
        ],
      })
    }
    
    // 从 HTML 中提取标题和描述
    const titleMatch = xhsHtml.match(/<title[^>]*>([^<]+)<\/title>/i)
    let title = titleMatch ? titleMatch[1].replace(' - 小红书', '').trim() : '小红书视频'
    console.log('Extracted title:', title)

    // 尝试多种方式提取描述
    let descriptionText: string | undefined
    
    // 方法1: 从 window.__INITIAL_STATE__ 或类似的数据结构中提取
    // 小红书可能使用 window.__INITIAL_STATE__ 或 window.__INITIAL_SSR_STATE__
    const initialStatePatterns = [
      /window\.__INITIAL_STATE__\s*=\s*({[\s\S]*?});/i,
      /window\.__INITIAL_SSR_STATE__\s*=\s*({[\s\S]*?});/i,
      /window\.__INITIAL_DATA__\s*=\s*({[\s\S]*?});/i,
    ]
    
    for (const pattern of initialStatePatterns) {
      const match = xhsHtml.match(pattern)
      if (match) {
        try {
          const initialState = JSON.parse(match[1])
          console.log('Found initial state, keys:', Object.keys(initialState))
          
          // 尝试从不同路径提取描述
          descriptionText = 
            initialState?.note?.note?.desc ||
            initialState?.note?.desc ||
            initialState?.noteData?.desc ||
            initialState?.data?.note?.desc ||
            initialState?.noteDetail?.note?.desc ||
            initialState?.noteDetail?.desc ||
            initialState?.note?.noteDetail?.desc
          
          if (descriptionText) {
            console.log('Extracted from initial state')
            break
          }
        } catch (e) {
          console.log('Failed to parse initial state:', e)
        }
      }
    }
    
    // 方法2: 从 JSON-LD 中提取
    if (!descriptionText) {
      const jsonLdRegex = /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi
      let match
      while ((match = jsonLdRegex.exec(xhsHtml)) !== null) {
        try {
          const jsonLd = JSON.parse(match[1])
          if (jsonLd.description) {
            descriptionText = jsonLd.description
            break
          }
        } catch (e) {
          // 继续尝试下一个
        }
      }
    }
    
    // 方法3: 从 meta description 中提取
    if (!descriptionText) {
      const metaDescMatch = xhsHtml.match(/<meta[^>]*name=["']description["'][^>]*content=["']([^"']+)["']/i)
      if (metaDescMatch) {
        descriptionText = metaDescMatch[1]
      }
    }
    
    // 方法4: 从页面数据中提取
    if (!descriptionText) {
      const descMatches = [
        /"desc":"([^"]+)"/,
        /"description":"([^"]+)"/,
        /"noteDesc":"([^"]+)"/,
        /"content":"([^"]+)"/,
      ]
      
      for (const pattern of descMatches) {
        const match = xhsHtml.match(pattern)
        if (match && match[1] && match[1].length > 10) {
          descriptionText = match[1]
          break
        }
      }
    }

    console.log('Extracted description:', descriptionText ? descriptionText.substring(0, 100) : 'none')

    // 如果没有描述，尝试使用标题或至少返回一些内容，避免完全失败
    if (!descriptionText || descriptionText.length < 5) {
      // 如果连标题都没有，使用默认值
      const finalTitle = title || '小红书视频'
      const fallbackDesc = `这是来自小红书视频 ${videoId} 的内容。由于无法提取详细描述，建议直接访问原视频查看完整内容。`
      
      // 返回一个基本的描述，让流程可以继续
      return res.status(200).json({
        title: finalTitle,
        descriptionText: fallbackDesc,
        subtitlesArray: [
          {
            text: fallbackDesc,
            index: 0,
            s: 0,
          },
        ],
      })
    }

    return res.status(200).json({
      title,
      descriptionText,
      subtitlesArray: [
        {
          text: descriptionText,
          index: 0,
          s: 0,
        },
      ],
    })
  } catch (error: any) {
    console.error('Error fetching xiaohongshu video:', error)
    return res.status(500).json({
      error: '获取失败',
      message: error.message || '获取小红书视频内容时发生错误',
    })
  }
}

