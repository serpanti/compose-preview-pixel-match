# Compose Preview Pixel Match

Скилл для реализации Jetpack Compose UI по Figma или PNG и доводки уже готовых экранов. Агент измеряет макет, рендерит выбранное состояние и повторяет сверку после каждой правки.

| Проверка | Что показывает |
| --- | --- |
| Геометрия | Красно-голубое наложение и IoU помогают проверить позиции, размеры и форму элементов. |
| Цвет | Отдельная карта отмечает изменения цвета и прозрачности. |
| Android | Финальная проверка в запущенном приложении учитывает системные панели, edge-to-edge и IME, когда они влияют на экран. |

## Установка в Codex

Linux и macOS:

```sh
git clone https://github.com/serpanti/compose-preview-pixel-match.git "${CODEX_HOME:-$HOME/.codex}/skills/compose-preview-pixel-match"
```

PowerShell:

```powershell
git clone https://github.com/serpanti/compose-preview-pixel-match.git "$env:USERPROFILE/.codex/skills/compose-preview-pixel-match"
```

Если Codex настроен на другой домашний каталог, используй его папку `skills`. После установки открой новый чат и передай агенту задачу:

> Используй $compose-preview-pixel-match. Реализуй карточку по этому макету либо доведи существующий Preview. Проверь форму, размеры, отступы и цвета.

Понадобится Android-проект со способом рендера Compose. Для структурированных замеров передай ссылку на Figma frame и подключи Figma. Если доступен только экспорт, передай PNG и целевой размер. Порядок работы описан в [SKILL.md](SKILL.md).

## Сравнить два PNG

Скрипт использует Python 3.10+ и стандартную библиотеку.

```sh
python scripts/contrast_compare.py --reference reference.png --candidate preview.png --output geometry.png --color-output color.png
```

Команда сохраняет две карты и печатает метрики в JSON. Параметр `--color-threshold` задаёт допустимую разницу 8-битных каналов. Цветовую карту удобнее читать после совмещения геометрии. Для проверки скрипта запусти `python -m unittest discover -s tests -v`.
