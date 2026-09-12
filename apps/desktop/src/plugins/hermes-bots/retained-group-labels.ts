import { useI18n } from '@hermes/plugin-sdk'

const messages = {
  en: {
    history: 'Retained history',
    readOnly: 'Read only',
    unavailable: 'This retained room is no longer available.',
    noBytes: 'File bytes are not retained on this Desktop.',
    source: 'Saved source',
    empty: 'No retained messages.'
  },
  ja: {
    history: '保存済みの履歴',
    readOnly: '読み取り専用',
    unavailable: 'この保存済みルームは利用できません。',
    noBytes: 'このデスクトップにファイルのデータは保存されていません。',
    source: '保存元',
    empty: '保存済みメッセージはありません。'
  },
  zh: {
    history: '保留的历史记录',
    readOnly: '只读',
    unavailable: '此保留的群聊已不可用。',
    noBytes: '此桌面未保留文件数据。',
    source: '保存的来源',
    empty: '没有保留的消息。'
  },
  'zh-hant': {
    history: '保留的歷史記錄',
    readOnly: '唯讀',
    unavailable: '此保留的群組已無法使用。',
    noBytes: '此桌面未保留檔案資料。',
    source: '儲存的來源',
    empty: '沒有保留的訊息。'
  },
  ar: {
    history: 'السجل المحفوظ',
    readOnly: 'للقراءة فقط',
    unavailable: 'هذه الغرفة المحفوظة لم تعد متاحة.',
    noBytes: 'بيانات الملف غير محفوظة على سطح المكتب هذا.',
    source: 'المصدر المحفوظ',
    empty: 'لا توجد رسائل محفوظة.'
  },
  ru: {
    history: 'Сохранённая история',
    readOnly: 'Только чтение',
    unavailable: 'Эта сохранённая группа больше недоступна.',
    noBytes: 'Данные файла не сохранены в этом Desktop.',
    source: 'Сохранённый источник',
    empty: 'Нет сохранённых сообщений.'
  }
}

export function useRetainedGroupLabels() {
  const { locale, t } = useI18n()

  return { ...(messages[locale as keyof typeof messages] ?? messages.en), back: t.common.back, locale }
}
