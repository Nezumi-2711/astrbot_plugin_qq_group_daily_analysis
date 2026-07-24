class InfoUtils:
    @staticmethod
    def get_user_nickname(config_manager, sender) -> str:
        """
        Lấy biệt danh thành viên.

        Ưu tiên trường nickname; nếu rỗng thì dùng card (tên trong nhóm).
        """
        enable_user_card = config_manager.get_enable_user_card()
        if enable_user_card:
            return (
                sender.get("card", "")
                or sender.get("nickname", "")
                or str(sender.get("user_id", ""))
            )
        else:
            return (
                sender.get("nickname", "")
                or sender.get("card", "")
                or str(sender.get("user_id", ""))
            )
