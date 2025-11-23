import React from 'react'
import { TypeAnimation } from 'react-type-animation'

export function TypingSlogan() {
  return (
    <div className="flex flex-col items-center justify-center">
      {/* VidAI 标题，加粗变大 */}
      <div className="mb-8 flex items-center gap-2">
        <h2 className="text-5xl font-bold text-gray-900 dark:text-gray-100 sm:text-6xl md:text-7xl">VidAI</h2>
      </div>
      
      {/* 主提示文字，小红书/抖音可以滚轮变换 */}
      <h1 className="mb-6 text-center text-4xl font-normal text-gray-900 dark:text-gray-100 sm:text-5xl">
        一键获取
        <span className="relative whitespace-nowrap text-pink-400">
          <TypeAnimation
            sequence={[
              '小红书',
              2000,
              '抖音',
              2000,
              () => {
                console.log('Done typing!')
              },
            ]}
            wrapper="span"
            cursor={true}
            repeat={Infinity}
            className="relative text-pink-400"
          />
        </span>
        视频修改建议
      </h1>
    </div>
  )
}
