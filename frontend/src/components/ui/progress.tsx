import { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

interface ProgressProps extends HTMLAttributes<HTMLDivElement> {
  value?: number
  indicatorClassName?: string
}

const Progress = ({
  value = 0,
  className,
  indicatorClassName,
  ...props
}: ProgressProps) => (
  <div
    role="progressbar"
    aria-valuemin={0}
    aria-valuemax={100}
    aria-valuenow={value}
    className={cn(
      'relative h-2 w-full overflow-hidden rounded-full bg-gray-100',
      className
    )}
    {...props}
  >
    <div
      className={cn('h-full rounded-full bg-primary transition-all', indicatorClassName)}
      style={{ width: `${value}%` }}
    />
  </div>
)

Progress.displayName = 'Progress'

export { Progress }
