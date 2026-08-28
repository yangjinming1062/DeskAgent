import js from '@eslint/js'
import typescriptEslint from '@typescript-eslint/eslint-plugin'
import typescriptParser from '@typescript-eslint/parser'
import perfectionist from 'eslint-plugin-perfectionist'
import reactPlugin from 'eslint-plugin-react'
import reactCompiler from 'eslint-plugin-react-compiler'
import hooksPlugin from 'eslint-plugin-react-hooks'
import unusedImports from 'eslint-plugin-unused-imports'
import globals from 'globals'

export default [
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      '**/dist-electron/**',
      'assets/**',
      'public/**',
      'src/**/*.js',
      'renderer/companion/puppet/vendor/**',
      '*.config.*'
    ]
  },
  js.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: typescriptParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
        ecmaVersion: 'latest',
        project: ['./tsconfig.json', './tsconfig.main.json'],
        sourceType: 'module',
        tsconfigRootDir: import.meta.dirname
      }
    },
    plugins: {
      '@typescript-eslint': typescriptEslint,
      perfectionist,
      'unused-imports': unusedImports
    },
    rules: {
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': [
        'error',
        {
          checksVoidReturn: {
            attributes: false
          }
        }
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          argsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_',
          destructuredArrayIgnorePattern: '^_',
          varsIgnorePattern: '^_'
        }
      ],
      curly: ['error', 'all'],
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-fallthrough': ['error', { allowEmptyCase: true }],
      'no-undef': 'off',
      'no-unused-vars': 'off',
      'padding-line-between-statements': [
        1,
        {
          blankLine: 'always',
          next: [
            'block-like',
            'block',
            'return',
            'if',
            'class',
            'continue',
            'debugger',
            'break',
            'multiline-const',
            'multiline-let'
          ],
          prev: '*'
        },
        {
          blankLine: 'always',
          next: '*',
          prev: ['case', 'default', 'multiline-const', 'multiline-let', 'multiline-block-like']
        },
        { blankLine: 'never', next: ['block', 'block-like'], prev: ['case', 'default'] },
        { blankLine: 'always', next: ['block', 'block-like'], prev: ['block', 'block-like'] },
        { blankLine: 'always', next: ['empty'], prev: 'export' },
        { blankLine: 'never', next: 'iife', prev: ['block', 'block-like', 'empty'] }
      ],
      'perfectionist/sort-exports': ['error', { order: 'asc', type: 'natural' }],
      'perfectionist/sort-imports': [
        'error',
        {
          groups: ['side-effect', 'builtin', 'external', 'internal', 'parent', 'sibling', 'index'],
          order: 'asc',
          type: 'natural'
        }
      ],
      'perfectionist/sort-jsx-props': ['error', { order: 'asc', type: 'natural' }],
      'perfectionist/sort-named-exports': ['error', { order: 'asc', type: 'natural' }],
      'perfectionist/sort-named-imports': ['error', { order: 'asc', type: 'natural' }],
      'unused-imports/no-unused-imports': 'error'
    }
  },
  {
    files: ['renderer/**/*.{ts,tsx}'],
    languageOptions: {
      globals: {
        ...globals.browser
      }
    },
    plugins: {
      react: reactPlugin,
      'react-compiler': reactCompiler,
      'react-hooks': hooksPlugin
    },
    rules: {
      ...reactPlugin.configs.recommended.rules,
      'react-compiler/react-compiler': 'warn',
      'react-hooks/exhaustive-deps': 'warn',
      'react-hooks/rules-of-hooks': 'error',
      'react/prop-types': 'off',
      'react/react-in-jsx-scope': 'off'
    },
    settings: {
      react: { version: 'detect' }
    }
  },
  {
    files: ['main/**/*.{ts,tsx}', 'scripts/**/*.{ts,tsx,mts}'],
    languageOptions: {
      globals: {
        ...globals.node
      }
    }
  },
  {
    files: ['renderer/companion/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@/hub', '@/hub/*', '../hub', '../hub/*', '../../hub', '../../hub/*'],
              message: 'companion must not import hub — review design before coupling the two windows.'
            }
          ]
        }
      ]
    }
  },
  {
    files: ['renderer/hub/**/*.{ts,tsx}'],
    ignores: ['**/node_modules/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '@/companion',
                '@/companion/*',
                '../companion',
                '../companion/*',
                '../../companion',
                '../../companion/*'
              ],
              message: 'hub must not import companion — review design before coupling the two windows.'
            }
          ]
        }
      ]
    }
  },
  {
    // 生产渲染面（精灵窗 / 工具窗 / 共享层）禁止裸 fetch：后端签名 URL 是相对路径，
    // 渲染进程 origin（dev 的 vite / 打包后的 file://）解析不到，请求会打到 vite 拿回
    // SPA 回退的 index.html。后端数据与字节一律走主进程桥（api / apiAsset /
    // apiAssetBuffer / apiAssetModelUrl）。独立调试页（puppet-entry / clip-debugger，
    // 部分在纯浏览器跑）不在此列；确需直连处逐行 eslint-disable 写明 URL 来源。
    files: ['renderer/companion/**/*.{ts,tsx}', 'renderer/hub/**/*.{ts,tsx}', 'renderer/shared/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "CallExpression[callee.name='fetch'], CallExpression[callee.type='MemberExpression'][callee.property.name='fetch']",
          message:
            '生产渲染面禁裸 fetch——后端相对 URL 在渲染进程 origin 上解析不到。走 window.spiritagent 的 api / apiAsset / apiAssetBuffer / apiAssetModelUrl 桥；例外逐行 eslint-disable 注明 URL 来源。'
        }
      ]
    }
  },
  {
    files: ['**/*.mjs', '**/vite.config.*', '**/vitest.config.*'],
    languageOptions: {
      ecmaVersion: 'latest',
      globals: { ...globals.node },
      sourceType: 'module'
    }
  },
  {
    files: ['**/*.js', '**/*.cjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      globals: { ...globals.node },
      sourceType: 'commonjs'
    }
  }
]
