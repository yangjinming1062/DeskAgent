import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

import ts from 'typescript'

const PRELOAD_PATH = path.join(import.meta.dirname, 'preload.ts')
const DECL_PATH = path.join(import.meta.dirname, '..', 'renderer', 'shared', 'types', 'global.d.ts')

const PRELOAD_SOURCE = fs.readFileSync(PRELOAD_PATH, 'utf8')
const DECL_SOURCE = fs.readFileSync(DECL_PATH, 'utf8')

function extractPreloadKeysFromAst(sourceText: string): Set<string> {
  const sourceFile = ts.createSourceFile('preload.ts', sourceText, ts.ScriptTarget.Latest, true)
  const keys = new Set<string>()

  function visit(node: ts.Node) {
    if (ts.isCallExpression(node)) {
      const expr = node.expression

      if (ts.isPropertyAccessExpression(expr) && expr.name.text === 'exposeInMainWorld' && node.arguments.length >= 2) {
        const firstArg = node.arguments[0]
        const secondArg = node.arguments[1]

        if (
          ts.isStringLiteral(firstArg) &&
          firstArg.text === 'spiritagent' &&
          ts.isObjectLiteralExpression(secondArg)
        ) {
          for (const prop of secondArg.properties) {
            if (prop.name) {
              if (ts.isIdentifier(prop.name) || ts.isStringLiteral(prop.name)) {
                keys.add(prop.name.text)
              }
            }
          }
        }
      }
    }

    ts.forEachChild(node, visit)
  }

  visit(sourceFile)

  return keys
}

function extractDeclKeysFromAst(sourceText: string): { optional: Set<string>; required: Set<string> } {
  const sourceFile = ts.createSourceFile('global.d.ts', sourceText, ts.ScriptTarget.Latest, true)
  const required = new Set<string>()
  const optional = new Set<string>()

  function visit(node: ts.Node) {
    if (ts.isInterfaceDeclaration(node) && node.name.text === 'Window') {
      for (const member of node.members) {
        if (
          ts.isPropertySignature(member) &&
          member.name &&
          ts.isIdentifier(member.name) &&
          member.name.text === 'spiritagent'
        ) {
          if (member.type && ts.isTypeLiteralNode(member.type)) {
            for (const spiritagentMember of member.type.members) {
              if (
                ts.isPropertySignature(spiritagentMember) &&
                spiritagentMember.name &&
                ts.isIdentifier(spiritagentMember.name)
              ) {
                const name = spiritagentMember.name.text

                if (spiritagentMember.questionToken) {
                  optional.add(name)
                } else {
                  required.add(name)
                }
              }
            }
          }
        }
      }
    }

    ts.forEachChild(node, visit)
  }

  visit(sourceFile)

  return { optional, required }
}

test('preload.ts exposes every required property declared in global.d.ts using TypeScript AST', () => {
  const exposed = extractPreloadKeysFromAst(PRELOAD_SOURCE)
  const { optional, required } = extractDeclKeysFromAst(DECL_SOURCE)

  assert.ok(exposed.size > 15, 'expected at least 15 exposed preload keys')
  assert.ok(required.size > 15, 'expected at least 15 required declared keys in global.d.ts')

  const missing = [...required].filter(k => !exposed.has(k))

  assert.deepEqual(
    missing,
    [],
    `preload.ts is missing required keys declared as non-optional in global.d.ts: ${missing.join(', ')}`
  )

  const undeclared = [...exposed].filter(k => !required.has(k) && !optional.has(k))

  assert.deepEqual(undeclared, [], `preload.ts exposes keys not declared in global.d.ts: ${undeclared.join(', ')}`)
})
