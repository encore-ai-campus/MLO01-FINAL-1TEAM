"""SQL 조각과 값 자리 채우기. 열람(`knowledge_engine.acl`)·현행 판 조건(`current_version`)이 같이 씀."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Sql:
    """SQL 조각. 값 자리는 `$?` 이고 번호는 `render` 가 매깁니다. 값을 글자로 이어 붙이지 않습니다."""
    text: str
    args: tuple = ()

    def __add__(self, other: "Sql") -> "Sql":
        return Sql(self.text + other.text, self.args + other.args)


def render(template: str, **params) -> tuple[str, list]:
    """`template` 의 `{이름}` 자리를 채우고 `$n` 을 앞에서부터 매깁니다.
    `Sql` 값은 조각 그대로(안의 `$?` 에 번호), 그 밖의 값은 값 자리 하나(같은 이름은 같은 번호).
    정해진 이름만 바꾸므로 다른 중괄호(`'{}'::jsonb` 등)는 그대로지만, 따옴표 안이라도 같은 이름이면 바뀝니다."""
    args: list = []
    slots: dict[str, str] = {}
    for name, value in params.items():
        if isinstance(value, Sql):
            parts = value.text.split("$?")
            if len(parts) - 1 != len(value.args):
                raise ValueError("값 자리 수와 값 수가 다릅니다")
            text = parts[0]
            for arg, part in zip(value.args, parts[1:]):
                args.append(arg)
                text += f"${len(args)}{part}"
            slots[name] = text
        else:
            args.append(value)
            slots[name] = f"${len(args)}"
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, slots)) + r")\}")
    return pattern.sub(lambda m: slots[m.group(1)], template), args
