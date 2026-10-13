import { type HTMLAttributes, type ImgHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const Avatar = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      'relative flex h-10 w-10 shrink-0 overflow-hidden rounded-full',
      className
    )}
    {...props}
  />
)

const AvatarImage = ({ className, ...props }: ImgHTMLAttributes<HTMLImageElement>) => (
  <img
    className={cn('aspect-square h-full w-full object-cover', className)}
    {...props}
  />
)

const AvatarFallback = ({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>) => (
  <span
    className={cn(
      'flex h-full w-full items-center justify-center rounded-full bg-muted text-sm font-medium text-muted-foreground',
      className
    )}
    {...props}
  />
)

export { Avatar, AvatarImage, AvatarFallback }
