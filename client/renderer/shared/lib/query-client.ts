import { QueryClient } from '@tanstack/react-query'

// 共享的 React Query 客户端。放在独立模块（而非 main.tsx）中，是为了让非 React
// 代码——例如网关切换时的 profile store——能在不引入应用入口的前提下失效缓存的、
// profile 作用域内的设置。
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 60_000
    }
  }
})
