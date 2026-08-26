# Рецепты

## 1. Одна съёмка → готовый ролик

```bash
VE=~/.claude/skills/video-edit/scripts/ve
cd /путь/к/съёмке
$VE probe .
$VE transcribe take1.mp4 --lang ru
$VE pack take1.mp4            # прочитай .ve/take1.transcript.md целиком
$VE autocut take1.mp4 --captions hormozi
# правишь edl.json руками
$VE render edl.json
$VE verify edit/final.mp4 --edl .ve/edl.resolved.json
$VE frames edit/final.mp4 -n 9
```

## 2. Вертикаль для Reels / Shorts / TikTok

```bash
$VE autocut take1.mp4 --width 1080 --height 1920 --fit crop \
    --captions hormozi --lufs -14 --output edit/reels.mp4
```

`crop` режет по краям от центра. Если говорящий смещён — задай кроп руками на клипе:

```json
{"vf": "crop=ih*9/16:ih:x=(iw-ih*9/16)*0.35:y=0"}
```

`x` — доля от 0 (левый край) до 1 (правый). Проверь одним кадром через
`$VE frames`, прежде чем рендерить всё.

## 3. Несколько дублей одной фразы

Транскрипт покажет их подряд. Автонарезка оставит последний. Если лучше был
первый — правь `in`/`out` в EDL руками, ориентируясь на таймкоды из
`transcript.md`. Визуально сравнить: `$VE frames take1.mp4 --at 41.2,58.7`.

## 4. Перебивка в середине реплики

Один клип режется на три, средний получает `v_src`:

```json
{"src": "/abs/take.mp4", "in": 12.30, "out": 14.05},
{"src": "/abs/take.mp4", "in": 14.05, "out": 17.20,
 "v_src": "/abs/broll.mp4", "v_in": 3.0, "captions": true},
{"src": "/abs/take.mp4", "in": 17.20, "out": 22.40}
```

Границы 14.05 и 17.20 бери **между словами** — смотри таймкоды в транскрипте.
Звук идёт непрерывно, рвётся только картинка, поэтому склейка не слышна.

## 5. Платформы

| площадка | холст | LUFS | субтитры |
|---|---|---|---|
| YouTube горизонт | 1920×1080 (или 2560×1440) | −14 | `standard` |
| YouTube Shorts / Reels / TikTok | 1080×1920 | −14 | `hormozi` |
| Подкаст / лекция | 1920×1080 | −16 | `minimal` |
| Инстаграм лента | 1080×1350 | −14 | `hormozi` |

## 6. Сырые ffmpeg-приёмы через `vf` / `af`

Движок собран на `ffmpeg-full`, доступно почти всё.

```json
"vf": "hqdn3d=4:3:6:6"                       // шумодав на тёмных планах
"vf": "unsharp=5:5:0.8"                      // подрезкость
"vf": "libplacebo=tonemapping=bt.2390"       // HDR → SDR
"af": "afftdn=nf=-30"                        // шумодав по звуку
"af": "highpass=f=80,lowpass=f=12000"        // почистить голос
"af": "acompressor=threshold=-18dB:ratio=3"  // компрессия голоса
```

Стабилизация — два прохода, вручную, до монтажа:

```bash
FF=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg
$FF -i shaky.mp4 -vf vidstabdetect=shakiness=8:result=tr.trf -f null -
$FF -i shaky.mp4 -vf vidstabtransform=input=tr.trf:smoothing=30,unsharp=5:5:0.8 \
   -c:v libx264 -crf 16 -c:a copy stable.mp4
```

Дальше монтируй `stable.mp4` как обычный исходник.

## 7. Что делать по итогам `verify`

| сообщение | причина | действие |
|---|---|---|
| `duration drift` | клипы потерялись или задвоились | сверь `.ve/edl.resolved.json` с `edl.json`; проверь `out` за пределами исходника |
| `A/V length mismatch` | у исходника звук короче видео | это чинится автоматически (`apad`); если всплыло — баг, смотри промежуточные в `.ve/cache/` |
| `true peak > -0.5 dBFS` | клиппинг | понизь `target_lufs` или добавь `"af": "alimiter=limit=0.9"` |
| `black span` | чёрные кадры на стыке | подвинь `in` первого клипа; часто это чёрный кадр в начале исходника |
| `silence >= 1.5s` | остался мёртвый воздух | уменьши `--max-gap` или подрежь клип; если тишина намеренная — объясни это пользователю |

## 8. Кэш

`.ve/cache/` хранит отрендеренные клипы по хэшу параметров. Меняешь один клип —
перерендерится только он. Кэш можно смело удалять. `.ve/*.words.json` —
результат ASR, удалять жалко: транскрипция самая долгая операция.
