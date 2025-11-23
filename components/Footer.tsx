import Link from 'next/link'
import { ModeToggle } from '~/components/mode-toggle'
import { Icons } from './icons'
import { buttonVariants } from '@/components/ui/button'

export default function Footer() {
  return (
    <footer className="z-50 mt-5 mb-3 flex h-16 w-full flex-col items-center justify-between space-y-3 bg-white px-3 pt-4 text-center text-slate-400 sm:mb-0 sm:h-20 sm:flex-row sm:justify-end sm:pt-2 lg:px-12">
      <div className="flex items-center space-x-1">
        <Link href="https://www.xiaohongshu.com/user/profile/61cfb4e1000000001000aea1" className="group" aria-label="小红书" target="_blank" rel="noopener noreferrer">
          <div
            className={buttonVariants({
              size: 'sm',
              variant: 'ghost',
              className: 'text-slate-700 dark:text-slate-400',
            })}
          >
            <Icons.xiaohongshu className="h-5 w-5 fill-current" />
            <span className="sr-only">小红书</span>
          </div>
        </Link>
        <Link href="https://x.com/ZhiCao01756604" className="group" aria-label="ZhiCao on Twitter">
          <div
            className={buttonVariants({
              size: 'sm',
              variant: 'ghost',
              className: 'text-slate-700 dark:text-slate-400',
            })}
          >
            <Icons.twitter className="h-5 w-5 fill-current" />
            <span className="sr-only">Twitter</span>
          </div>
        </Link>
        <Link href="https://github.com/ExcitedButter" className="group" aria-label="ExcitedButter on GitHub">
          <div
            className={buttonVariants({
              size: 'sm',
              variant: 'ghost',
              className: 'text-slate-700 dark:text-slate-400',
            })}
          >
            <Icons.gitHub className="h-5 w-5" />
            <span className="sr-only">GitHub</span>
          </div>
        </Link>
        <ModeToggle />
      </div>
    </footer>
  )
}
