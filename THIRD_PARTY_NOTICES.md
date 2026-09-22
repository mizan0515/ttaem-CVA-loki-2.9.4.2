# 외부 구성요소와 조건

TTaem 소유 코드·프롬프트·로고에 대한 권한은 권리자가 확인했다. 독립적인 외부 구성요소는 각각의 원 라이선스를 유지한다. 프로젝트의 제한 정책이 이들 구성요소의 원래 권리를 대신하지 않는다.

## 소스와 함께 포함하는 외부 코드

- **hls.js 1.7.2**, Apache-2.0. `ttaem_cva/web/static/admin/vendor/hls.min.js`는 공식 npm 배포 파일과 바이트가 동일하다. SHA-256 `afcde07437ec84b072fe8782e772ceb5046eac751b2719f73ae0d83d763bc3f5`. Copyright (c) 2017 Dailymotion; 일부 파생 코드 Copyright (c) 2013-2015 Brightcove. 원문 [라이선스](licenses/hls.js-LICENSE.txt)와 [Apache-2.0 전문](licenses/Apache-2.0.txt)을 함께 제공한다. [공식 소스](https://github.com/video-dev/hls.js/tree/v1.7.2).
- **3D-Speaker 계열 ERes2NetV2 인코더 구조**, Apache-2.0. `ttaem_cva/subtitle_enhancement/reference.py`는 3.0.1에서 선별한 관찰 전용 어댑터이며 파일의 출처·저작권 고지를 보존한다. 학습·합성·TTS를 포함하지 않는다. [공식 LICENSE](https://github.com/modelscope/3D-Speaker/blob/main/LICENSE), 동봉한 Apache-2.0 전문을 따른다. 해당 파일은 선별 원본과 동일하고 외부 가중치를 동봉하지 않는다.

- **Chart.js 4.4.2**, MIT. 기존 템플릿의 채팅 밀도 그래프에 사용한 고정 배포 자산을 유지한다. [동봉 LICENSE](licenses/Chart.js-LICENSE.md), [공식 소스](https://github.com/chartjs/Chart.js/tree/v4.4.2).

## 별도로 설치하거나 다운로드하는 의존성

| 구성요소 | 용도와 원 조건 | 배포 방식 |
|---|---|---|
| Python | 실행환경, Python Software Foundation License | 이용자 환경에 별도 설치 |
| Flask | 로컬 관리자 서버, BSD-3-Clause | PyPI 패키지의 LICENSE 보존 |
| Playwright 1.63.0 | 익명 공개 클립의 원본 시각 확인, Apache-2.0 | PyPI 별도 설치, 패키지의 LICENSE·포함 고지 보존 |
| greenlet 3.5.6 / pyee 13.0.1 | Playwright 실행 의존성, 각각 MIT AND PSF-2.0 / MIT | 설치 패키지의 원 라이선스 보존 |
| Playwright Chromium | 공개 페이지 확인용 브라우저와 포함 라이브러리별 조건 | `.models/playwright`에 별도 다운로드, 소스 배포에 바이너리 제외 |
| Beautiful Soup 4.15.0 / SoupSieve 2.9.2 | 원본 커뮤니티 HTML 파싱·선택, MIT | PyPI 별도 설치, 원 고지 보존 |
| lxml 6.1.3 | HTML 파서, BSD-3-Clause 및 포함 libxml2/libxslt 등의 조건 | PyPI 별도 설치, wheel에 포함된 고지 보존 |
| faster-whisper / CTranslate2 | 로컬 전사, MIT | PyPI에서 별도 설치 |
| Whisper large-v3-turbo 변환 가중치 | 원 Whisper 및 변환 배포의 MIT 표시 | 최초 전사 시 사용자 로컬 캐시에 다운로드, 저장소에는 제외 |
| PyTorch / torchaudio | 선택 GPU·참조 음성 인코더 런타임, 각 배포물의 BSD 계열 및 포함 라이브러리 조건 | 선택 설치, GPU 바이너리의 별도 조건 유지 |
| FFmpeg | 오디오 다운로드·디코딩, 실제 빌드에 따라 LGPL/GPL 등 | 별도 설치; 이 프로젝트는 바이너리를 배포하지 않음 |

검증 버전은 `requirements.txt`와 `requirements-lock.txt`에 기록한다. 설치 패키지의 전이 의존성과 포함 라이브러리 고지도 각 배포물에 남는다. 별도 바이너리 번들 제품을 만들 경우 현재의 소스 배포 감사만으로 완료됐다고 보지 않는다.

공식 근거: [Flask LICENSE](https://github.com/pallets/flask/blob/main/LICENSE.txt), [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2), [Whisper LICENSE](https://github.com/openai/whisper/blob/main/LICENSE), [변환 모델](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo), [FFmpeg legal](https://ffmpeg.org/legal.html).

클립 수집 의존성 근거: [Playwright LICENSE](https://github.com/microsoft/playwright-python/blob/main/LICENSE), [greenlet LICENSE](https://github.com/python-greenlet/greenlet/blob/master/LICENSE), [pyee LICENSE](https://github.com/jfhbrook/pyee/blob/main/LICENSE). 설치된 고정 버전의 패키지 메타데이터와 라이선스 파일도 확인했다. 브라우저는 매 실행 새 임시 컨텍스트를 사용하고 개인 브라우저 프로필·쿠키·로그인을 가져오지 않는다.

커뮤니티 파서 근거: [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/#license), [lxml FAQ](https://lxml.de/FAQ.html#what-is-the-license), [SoupSieve LICENSE](https://github.com/facelessuser/soupsieve/blob/main/LICENSE.md). 설치한 고정 버전의 패키지 메타데이터도 확인했다.

## 선택 음성 참조의 추가 조건

ERes2NetV2 가중치 후보 `iic/speech_eres2netv2w24s4ep4_sv_zh-cn_16k-common`의 정확한 revision·가중치 조건은 이 배포에서 확정하지 않았다. 자동 다운로드나 권리 허가를 제공하지 않는다. 사용자가 공식 출처·revision·라이선스·체크섬과 녹음 사용 권한을 확인해 로컬 자료로 준비한 경우에만 선택적으로 활성화한다. 코드의 Apache 조건을 가중치나 개인 녹음에 추정 적용하지 않는다.

AST 음악/이벤트 모델은 현재 공개판 실행 경로에 포함하지 않는다. 그 가중치·실험 자료·개인 녹음·프로필·벡터도 배포하지 않는다.
