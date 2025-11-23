import Image from 'next/image'
import React from 'react'

export function SubmitButton({ loading }: { loading: boolean }) {
  if (!loading) {
    return (
      <button
        className="z-10 mx-auto mt-7 w-3/4 rounded-2xl border-gray-500 bg-black p-3 text-lg font-medium text-white transition hover:bg-gray-800 sm:mt-10 sm:w-1/3 dark:bg-gray-900 dark:hover:bg-gray-700"
        type="submit"
      >
        一键获取
      </button>
    )
  }

  return (
    <button
      className="z-10 mx-auto mt-7 w-3/4 cursor-not-allowed rounded-2xl border-gray-500 bg-black p-3 text-lg font-medium transition hover:bg-gray-800 sm:mt-10 sm:w-1/3 dark:bg-gray-900 dark:hover:bg-gray-700"
      disabled
    >
      <div className="flex items-center justify-center text-white">
        <Image src="/loading.svg" alt="Loading..." width={28} height={28} />
      </div>
    </button>
  )
}
