import json, sys
base = sys.argv[1]
s = json.load(open(base + '/sessions.json'))
print('snapshot', s['t'], 'retired:', s.get('retired'))
for x in s['sessions']:
    chat = [m for m in x['messages'] if m[0] in ('user', 'assistant')]
    if chat and chat[0][1] and chat[0][1].startswith('### Task'):
        print('  task ', x['id'], (chat[0][1] or '')[:40].replace('\n', ' '))
        continue
    print('  CHAT ', x['id'], [m[0][0] + ':' + (m[1] or '')[:30] for m in chat])
