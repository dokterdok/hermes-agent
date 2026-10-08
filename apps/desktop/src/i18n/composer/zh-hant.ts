import type { TranslationOverrides } from '../define-locale'
import { zhHantChat } from '../zh-hant_chat'

export const zhHantComposer: NonNullable<TranslationOverrides['composer']> = {
  draftReadFailed: '無法讀取已儲存的草稿。儲存內容未被修改；恢復可用後，請重試。',
  ...zhHantChat.composer,
  redirect: '重新導向目前的執行',
  queueLostNote: '重新啟動期間該回合已遺失',
  restoreImageDraft: '還原草稿',
  queueLostDiscard: '捨棄',
  queueLostDiscardTip: '閘道在該回合進行中重新啟動，無法完成。捨棄後其後的排隊回合將繼續執行。'
}
