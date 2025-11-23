import { Poppins } from '@next/font/google'
import clsx from 'clsx'
import React from 'react'
import Github from '../components/GitHub'
const poppins = Poppins({ weight: '800', subsets: ['latin'] })

export default function Header({ showSingIn }: { showSingIn: (show: boolean) => void }) {
  return (
    <header className="sticky top-0 z-40 w-full flex-none border-b border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div className="flex items-center justify-between px-4 py-3 sm:px-6">
        <div className="flex items-center space-x-3">
          <h2 className={clsx('text-2xl sm:text-3xl font-semibold text-gray-900 dark:text-white', poppins.className)}>
            VidAI
          </h2>
        </div>
        <div className="flex shrink-0 items-center space-x-4">
          <a href="https://github.com/JimmyLv/BibiGPT" rel="noreferrer noopener" target="_blank" className="text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white">
            <Github width="24" height="24" />
          </a>
        </div>
      </div>·
    </header>
  )
}
