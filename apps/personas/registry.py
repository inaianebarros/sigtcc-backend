from .rules import PersonaRule
from .simulated_students import SIMULATED_STUDENT_RULES
from .simulated_teacher import SIMULATED_TEACHER_RULES

RULES: list[PersonaRule] = [*SIMULATED_TEACHER_RULES, *SIMULATED_STUDENT_RULES]
