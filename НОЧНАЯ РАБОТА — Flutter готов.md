# 🚀 Ночная работа завершена — Flutter Mobile готов

> Создано: 11 июля 2026, пока ты спал

## Стек выбран: Flutter

**Почему Flutter, а не React Native:**
- CustomPainter для waveform — без JS-моста, нативная плавность
- Запись аудио без задержек (record package, 16kHz mono .m4a — то что нужно Whisper)
- Pixel-perfect перенос цветов из styles.css в Dart
- Sino AI, скорее всего, тоже на Flutter

---

## Что создано

### 1. Plan документ (1103 строки)
```
~/Avris/AvrisAI Knowledge Base/Development/Mobile-App-Plan.md
```
- Полная карта всех API-эндпоинтов бэкенда
- Все JSON схемы запросов и ответов
- Структура папок mobile/lib/
- pubspec.yaml со всеми зависимостями
- 5-дневный план разработки
- Критические моменты iOS/Android

### 2. Flutter scaffold (24 Dart файла)
```
~/Avris/mobile/
├── pubspec.yaml
├── lib/
│   ├── main.dart
│   ├── core/theme/
│   │   ├── app_colors.dart      # все цвета из CSS (#4AA391 и др.)
│   │   └── app_theme.dart       # ThemeData dark + light
│   ├── core/constants/
│   │   └── api_constants.dart   # все эндпоинты
│   ├── services/
│   │   ├── api_service.dart     # Dio + JWT Bearer + auto-refresh на 401
│   │   ├── audio_service.dart   # запись .m4a 16kHz mono для Whisper
│   │   └── local_storage.dart   # FlutterSecureStorage (токены)
│   ├── models/                  # 8 Freezed-моделей
│   │   ├── user.dart
│   │   ├── patient.dart
│   │   ├── consultation.dart
│   │   ├── soap_response.dart   # + ai_recommendations с ICD-10
│   │   ├── lab_order.dart
│   │   ├── night_round.dart
│   │   ├── dashboard_stats.dart
│   │   └── notification_model.dart
│   ├── features/
│   │   ├── auth/
│   │   │   ├── providers/auth_provider.dart   # AuthNotifier, is_approved gate
│   │   │   └── screens/login_screen.dart
│   │   ├── dashboard/
│   │   │   ├── providers/dashboard_provider.dart
│   │   │   ├── screens/dashboard_screen.dart  # 3 карточки + список + FAB
│   │   │   └── widgets/
│   │   │       ├── stat_card.dart
│   │   │       └── patient_list_tile.dart
│   │   └── consultation/
│   │       ├── screens/consultation_screen.dart  # запись→STT→SOAP полный флоу
│   │       └── widgets/voice_recorder_widget.dart # waveform + pulse анимация
│   └── router/
│       └── app_router.dart      # GoRouter + auth guard (redirect)
```

---

## Для запуска

```bash
cd ~/Avris/mobile
flutter pub get
dart run build_runner build --delete-conflicting-outputs
flutter run --dart-define=API_BASE_URL=http://localhost:8000
```

---

## 5-дневный план

| День | Задачи |
|------|--------|
| **День 1 (сегодня)** | Register screen, OTP verify, ForgotPassword, History screen, Settings (профиль + аватар + язык) |
| День 2 | Dashboard доработка, Patient modal (карточка), поиск и фильтры |
| День 3 | Lab Connect QR modal, Night Round screen, PDF-экспорт |
| День 4 | i18n ARB файлы (ru/tj/en), push notifications (FCM) |
| День 5 | TestFlight / Play Console, smoke tests, деплой |

---

## Ключевые зависимости (pubspec.yaml)

```yaml
flutter_riverpod: ^2.5.1
go_router: ^14.2.7
dio: ^5.4.3+1
freezed_annotation: ^2.4.4
json_annotation: ^4.9.0
flutter_secure_storage: ^9.2.2
record: ^5.1.2
permission_handler: ^11.3.1
qr_flutter: ^4.1.0
intl: ^0.19.0
```

---

*Готов начинать День 1 когда скажешь.*
