export interface SavedGroupMessages {
  viewSavedCopies: string
  savedCopies: string
  savedCopy: string
  savedCopyPartial: string
  savedCopiesEmpty: string
  savedCopiesLoading: string
  savedCopiesUnavailable: string
  savedCopiesDenied: string
  savedCopiesOffline: string
  savedCopiesFailed: string
  savedCopyPreview: string
  savedPreviewLoading: string
  savedPreviewFailed: string
  savedCopyReceived: string
  savedCopyIncomplete: string
  savedCopyNeedsReview: string
  savedCopyRetired: string
  savedGroupEnded: string
  savedCopiesNotResumed: string
  savedCopiesRecentWork: string
  savedWorkUnknown: string
  savedWorkReconciliation: string
  savedWorkTasks: string
  savedWorkReceipts: string
  savedCopiesPrevious: string
  savedCopiesNext: string
}

export const SAVED_GROUP_LOCALES = {
  en: {
    viewSavedCopies: 'View saved copies', savedCopies: 'Saved copies', savedCopy: 'Saved copy',
    savedCopyPartial: 'Incomplete copy',
    savedCopiesEmpty: 'No saved copies on this gateway.', savedCopiesLoading: 'Loading saved copies…',
    savedCopiesUnavailable: 'Saved copies are unavailable on this connection.',
    savedCopiesDenied: 'You do not have access to saved copies on this gateway.',
    savedCopiesOffline: 'This gateway is disconnected.', savedCopiesFailed: 'Saved copies could not be read.',
    savedCopyPreview: 'Saved copy details', savedPreviewLoading: 'Checking this saved copy…',
    savedPreviewFailed: 'This saved copy could not be checked. Refresh the list before selecting it again.',
    savedCopyReceived: 'Copy received', savedCopyIncomplete: 'Some recent activity is missing from this copy.',
    savedCopyNeedsReview: 'Needs review', savedCopyRetired: 'Copy retired', savedGroupEnded: 'Group ended',
    savedCopiesNotResumed: 'Work has not been resumed.', savedCopiesRecentWork: 'Recent work may be missing.',
    savedWorkUnknown: 'The saved work record is incomplete or unavailable.',
    savedWorkReconciliation: 'Recorded work still needs reconciliation before any recovery.',
    savedWorkTasks: 'Tasks in this work record', savedWorkReceipts: 'Receipts in this work record',
    savedCopiesPrevious: 'Previous saved copies', savedCopiesNext: 'More saved copies'
  },
  ja: {
    viewSavedCopies: '保存済みコピーを表示', savedCopies: '保存済みコピー', savedCopy: '保存済みコピー',
    savedCopyPartial: '不完全なコピー',
    savedCopiesEmpty: 'このゲートウェイに保存済みコピーはありません。', savedCopiesLoading: '保存済みコピーを読み込み中…',
    savedCopiesUnavailable: 'この接続では保存済みコピーを利用できません。',
    savedCopiesDenied: 'このゲートウェイの保存済みコピーへのアクセス権がありません。',
    savedCopiesOffline: 'このゲートウェイは切断されています。', savedCopiesFailed: '保存済みコピーを読み取れませんでした。',
    savedCopyPreview: '保存済みコピーの詳細', savedPreviewLoading: '保存済みコピーを確認中…',
    savedPreviewFailed: '保存済みコピーを確認できませんでした。一覧を更新してから再度選択してください。',
    savedCopyReceived: 'コピーの受信日時', savedCopyIncomplete: 'このコピーには最近の履歴の一部がありません。',
    savedCopyNeedsReview: '要確認', savedCopyRetired: '保持終了のコピー', savedGroupEnded: 'グループは終了しています',
    savedCopiesNotResumed: '処理は再開されていません。', savedCopiesRecentWork: '最近の処理が記録されていない可能性があります。',
    savedWorkUnknown: '保存された処理記録は不完全か、利用できません。',
    savedWorkReconciliation: '復旧の前に、記録された処理の照合が必要です。',
    savedWorkTasks: 'この処理記録のタスク数', savedWorkReceipts: 'この処理記録の受領記録数',
    savedCopiesPrevious: '前の保存済みコピー', savedCopiesNext: '次の保存済みコピー'
  },
  zh: {
    viewSavedCopies: '查看已保存的副本', savedCopies: '已保存的副本', savedCopy: '已保存的副本',
    savedCopyPartial: '不完整的副本',
    savedCopiesEmpty: '此网关没有已保存的副本。', savedCopiesLoading: '正在加载已保存的副本…',
    savedCopiesUnavailable: '此连接无法访问已保存的副本。', savedCopiesDenied: '你无权访问此网关上已保存的副本。',
    savedCopiesOffline: '此网关已断开连接。', savedCopiesFailed: '无法读取已保存的副本。',
    savedCopyPreview: '已保存副本的详情', savedPreviewLoading: '正在检查此副本…',
    savedPreviewFailed: '无法检查此副本。请刷新列表后重新选择。',
    savedCopyReceived: '副本接收时间', savedCopyIncomplete: '此副本缺少部分近期活动。',
    savedCopyNeedsReview: '需要检查', savedCopyRetired: '副本已退役', savedGroupEnded: '群组已结束',
    savedCopiesNotResumed: '工作尚未恢复。', savedCopiesRecentWork: '近期工作记录可能缺失。',
    savedWorkUnknown: '已保存的工作记录不完整或不可用。', savedWorkReconciliation: '恢复前仍需核对已记录的工作。',
    savedWorkTasks: '此工作记录中的任务', savedWorkReceipts: '此工作记录中的回执',
    savedCopiesPrevious: '上一页副本', savedCopiesNext: '更多副本'
  },
  'zh-hant': {
    viewSavedCopies: '檢視已儲存的副本', savedCopies: '已儲存的副本', savedCopy: '已儲存的副本',
    savedCopyPartial: '不完整的副本',
    savedCopiesEmpty: '此閘道沒有已儲存的副本。', savedCopiesLoading: '正在載入已儲存的副本…',
    savedCopiesUnavailable: '此連線無法存取已儲存的副本。', savedCopiesDenied: '你無權存取此閘道上已儲存的副本。',
    savedCopiesOffline: '此閘道已中斷連線。', savedCopiesFailed: '無法讀取已儲存的副本。',
    savedCopyPreview: '已儲存副本的詳細資料', savedPreviewLoading: '正在檢查此副本…',
    savedPreviewFailed: '無法檢查此副本。請重新整理清單後再次選取。',
    savedCopyReceived: '副本接收時間', savedCopyIncomplete: '此副本缺少部分近期活動。',
    savedCopyNeedsReview: '需要檢查', savedCopyRetired: '副本已退役', savedGroupEnded: '群組已結束',
    savedCopiesNotResumed: '工作尚未恢復。', savedCopiesRecentWork: '近期工作記錄可能缺失。',
    savedWorkUnknown: '已儲存的工作記錄不完整或無法使用。', savedWorkReconciliation: '恢復前仍需核對已記錄的工作。',
    savedWorkTasks: '此工作記錄中的任務', savedWorkReceipts: '此工作記錄中的回執',
    savedCopiesPrevious: '上一頁副本', savedCopiesNext: '更多副本'
  },
  ar: {
    viewSavedCopies: 'عرض النسخ المحفوظة', savedCopies: 'النسخ المحفوظة', savedCopy: 'نسخة محفوظة',
    savedCopyPartial: 'نسخة غير مكتملة',
    savedCopiesEmpty: 'لا توجد نسخ محفوظة على هذه البوابة.', savedCopiesLoading: 'جارٍ تحميل النسخ المحفوظة…',
    savedCopiesUnavailable: 'النسخ المحفوظة غير متاحة عبر هذا الاتصال.', savedCopiesDenied: 'ليس لديك إذن للوصول إلى النسخ المحفوظة على هذه البوابة.',
    savedCopiesOffline: 'هذه البوابة غير متصلة.', savedCopiesFailed: 'تعذرت قراءة النسخ المحفوظة.',
    savedCopyPreview: 'تفاصيل النسخة المحفوظة', savedPreviewLoading: 'جارٍ فحص هذه النسخة…',
    savedPreviewFailed: 'تعذر فحص هذه النسخة. حدّث القائمة ثم اخترها مجددًا.',
    savedCopyReceived: 'تاريخ استلام النسخة', savedCopyIncomplete: 'بعض الأنشطة الحديثة مفقودة من هذه النسخة.',
    savedCopyNeedsReview: 'تحتاج إلى مراجعة', savedCopyRetired: 'نسخة متقاعدة', savedGroupEnded: 'انتهت المجموعة',
    savedCopiesNotResumed: 'لم يُستأنف العمل.', savedCopiesRecentWork: 'قد تكون سجلات العمل الحديثة مفقودة.',
    savedWorkUnknown: 'سجل العمل المحفوظ غير مكتمل أو غير متاح.', savedWorkReconciliation: 'يجب التحقق من العمل المسجل ومطابقته قبل أي استعادة.',
    savedWorkTasks: 'المهام في سجل العمل هذا', savedWorkReceipts: 'إيصالات التأكيد في سجل العمل هذا',
    savedCopiesPrevious: 'النسخ المحفوظة السابقة', savedCopiesNext: 'المزيد من النسخ المحفوظة'
  },
  ru: {
    viewSavedCopies: 'Посмотреть сохранённые копии', savedCopies: 'Сохранённые копии', savedCopy: 'Сохранённая копия',
    savedCopyPartial: 'Неполная копия',
    savedCopiesEmpty: 'На этом шлюзе нет сохранённых копий.', savedCopiesLoading: 'Загрузка сохранённых копий…',
    savedCopiesUnavailable: 'Сохранённые копии недоступны через это подключение.', savedCopiesDenied: 'У вас нет доступа к сохранённым копиям на этом шлюзе.',
    savedCopiesOffline: 'Этот шлюз отключён.', savedCopiesFailed: 'Не удалось прочитать сохранённые копии.',
    savedCopyPreview: 'Сведения о сохранённой копии', savedPreviewLoading: 'Проверка сохранённой копии…',
    savedPreviewFailed: 'Не удалось проверить копию. Обновите список и выберите её снова.',
    savedCopyReceived: 'Копия получена', savedCopyIncomplete: 'В этой копии отсутствует часть недавней активности.',
    savedCopyNeedsReview: 'Требует проверки', savedCopyRetired: 'Копия выведена из использования', savedGroupEnded: 'Группа завершена',
    savedCopiesNotResumed: 'Работа не возобновлена.', savedCopiesRecentWork: 'Недавняя работа может отсутствовать.',
    savedWorkUnknown: 'Сохранённая запись о работе неполна или недоступна.', savedWorkReconciliation: 'Перед восстановлением нужно сверить записанную работу.',
    savedWorkTasks: 'Задачи в этой записи о работе', savedWorkReceipts: 'Подтверждения в этой записи о работе',
    savedCopiesPrevious: 'Предыдущие сохранённые копии', savedCopiesNext: 'Следующие сохранённые копии'
  }
} satisfies Record<string, SavedGroupMessages>
