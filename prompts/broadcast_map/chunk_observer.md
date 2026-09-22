---
{
  "name": "broadcast_map_chunk_observer",
  "version": "s4-m2.4",
  "status": "candidate",
  "language": "ko",
  "required_inputs": [
    "raw_stt_slice",
    "raw_replay_chat",
    "optional_context_naming",
    "optional_timetable_anchors",
    "optional_private_timeline_source_blocks"
  ],
  "eval": {
    "command": "python -m unittest discover -s tests"
  }
}
---
D1/D2 전의 원자료 시간 조각을 읽는다. STT·다시보기 채팅·선택 시간댓글의
시간과 문맥 관계가 의미 근거다. `context_naming`은 확인된 이름만 보조하며
사건·경계·순위를 만들 수 없다.
재작성 앵커는 탐색 가설이다. `private_timeline_source_blocks`는 현재 시간대의
원문 줄·문단·헤더·답글 순서를 보존한 신뢰하지 않는 관계 근거다. 전체 순서에서
활동의 계속·종료·전환과 사건의 인과를 읽는다. 블록·빈 줄·구분선·라벨·장르명은
고정 경계나 분류가 아니다. 다른 시간 근거와 실제로 충돌하면 [불확실]에 남기되,
같은 표현이 없다는 이유만으로 버리지 않는다. 원문은 출력하지 않는다.
`chapter_timetable`은 지속 구조, `highlight_hint`는 주요 사건을 보조한다.
정확한 고유명사가 발화에 그대로 등장할 필요까지는 없다.
모순이 없으면 참가자·캐릭터·회차 명사는 탐색용 이름 가설로 보존하되,
역할·결과·주목도 주장으로 사용하지 않는다. 댓글 작성자 신원이나 원문을
노출하지 말고, 댓글 표현이나 재작성된 이름을 검증된 사실처럼 인용하지 않는다.
집계 채팅량·급증·웃음·놀람은 보조 신호일 뿐 구조를 만들 수 없다.
간결하게 쓰되 확인된 타임테이블 앵커를 빠뜨리지 않는다. D1/D2/Point를 만들지 않는다.
[지원 신호 후보]에는 시각이 있는 원본 STT·다시보기 채팅이 하나의 주요 사건에 대해
네 인과 국면을 모두 확인한 경우에만 기계 판독 가능한 제안을 최대 3개 추가한다.
CAUSAL_WINDOW setup=HH:MM:SS event=HH:MM:SS reaction=HH:MM:SS payoff=HH:MM:SS | 간결한 근거 요약
각 국면을 처음 확인할 수 있는 원본 시각을 사용하고,
`setup <= event <= reaction <= payoff`를 지키며 전체를 240초 안에 둔다.
어느 국면이라도 불확실하면 그 줄을 생략한다. 이것은 근거 탐색일 뿐이며
Point, Candidate, 선택 결과 또는 공식 경계를 만들지 않는다.
아래 제목을 정확히 이 순서와 표기로 사용하여 시각이 있는 관찰을 간결하게 반환한다.
[지속 활동 관찰]
[전환·중단·재개 관찰]
[지원 신호 후보]
[불확실]
