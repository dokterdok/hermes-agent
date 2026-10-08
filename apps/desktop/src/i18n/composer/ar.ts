import { arChat } from '../ar_chat'
import type { TranslationOverrides } from '../define-locale'

export const arComposer: NonNullable<TranslationOverrides['composer']> = {
  draftReadFailed: 'تعذّر قراءة المسودات المحفوظة. لم يتم تعديل التخزين؛ حاول مجددًا عندما يصبح متاحًا.',
  ...arChat.composer,
  redirect: 'إعادة توجيه التشغيل الحالي',
  queueLostNote: 'فُقد الدور أثناء إعادة التشغيل',
  restoreImageDraft: 'استعادة المسودة',
  queueLostDiscard: 'تجاهل',
  queueLostDiscardTip: 'أعادت البوابة التشغيل أثناء هذا الدور ولا يمكن إكماله. تجاهله لتستمر الأدوار المنتظرة خلفه.'
}
