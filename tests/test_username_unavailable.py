"""Tên người dùng bị Instagram từ chối phải dừng hẳn, không lặp vô hạn.

Runner báo về ``retry_pending`` kèm lý do ``USERNAME_UNAVAILABLE``. Trước đây
controller gộp mọi ``retry_pending`` không rõ nguyên nhân thành ``UI_CHANGED``
rồi đưa tài khoản về ``RETRY_PENDING`` -- mà ``RETRY_PENDING`` lại là trạng thái
được ``assign_next`` giao việc tiếp, nên hệ thống cứ giao lại đúng cái tên đã bị
từ chối. Vòng lặp này nện liên tục vào Instagram mà không bao giờ tiến được.

``USERNAME_UNAVAILABLE`` đã có sẵn trong state machine và KHÔNG nằm trong danh
sách trạng thái được giao việc, nên dừng ở đó là đúng: chờ người vận hành (hoặc
một bước cấp tên khác) đặt tên mới.
"""
import unittest

from acp.core.factory_v2.models import AccountStage as S
from acp.core.factory_v2.state_machine import can_transition


class UsernameUnavailableTransitionTests(unittest.TestCase):
    def test_runner_dang_chay_co_the_bao_ten_khong_dung_duoc(self):
        self.assertTrue(
            can_transition(S.RUNNER_ASSIGNED, S.USERNAME_UNAVAILABLE),
            "runner đang chạy phát hiện tên bị từ chối thì phải báo được trạng thái đó",
        )

    def test_van_con_duong_ra_de_dat_ten_khac(self):
        for target in (S.IG_READY_FOR_HUMAN, S.WAITING_HUMAN, S.RETRY_PENDING):
            with self.subTest(den=target.value):
                self.assertTrue(
                    can_transition(S.USERNAME_UNAVAILABLE, target),
                    "phải quay lại được sau khi đặt tên khác",
                )


if __name__ == "__main__":
    unittest.main()
