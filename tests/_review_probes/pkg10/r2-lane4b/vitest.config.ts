import path from 'node:path'
import base from '../../../../apps/desktop/vitest.config'

const root = path.resolve(import.meta.dirname, '../../../../apps/desktop')
const ui = base.test!.projects![0] as any
export default {
  ...base,
  root,
  test: {
    ...base.test,
    projects: [{
      ...ui,
      extends: path.join(root, 'vite.config.ts'),
      test: {
        ...ui.test,
        setupFiles: [path.join(root, 'vitest.setup.ts')],
        include: [path.join(import.meta.dirname, 'archive-loader.test.ts').replaceAll('\\', '/')],
      },
    }],
  },
}
