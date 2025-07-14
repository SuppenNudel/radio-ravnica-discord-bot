from ezcord import log, Cog
from discord.ext.commands import slash_command, has_role, has_permissions
from discord import ApplicationContext, Bot, default_permissions, InteractionContextType, IntegrationType
from collections import defaultdict

class FormatOverlapCheck(Cog):
    def __init__(self, bot:Bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        # for guild in self.bot.guilds:
        #     for member in guild.members:
        #         matching_roles = [role for role in member.roles if role.name.startswith("Turnierspieler")]
        #         if matching_roles:
        #             log.debug(f"Member '{member.name}' in guild '{guild.name}' has roles: {[role.name for role in matching_roles]}")
        # Calculate overlap between "Turnierspieler" roles
        role_members = defaultdict(set)
        for guild in self.bot.guilds:
            for member in guild.members:
                for role in member.roles:
                    if role.name.startswith("Turnierspieler"):
                        role_members[role.name].add(member.id)
        overlap_results = {}
        role_names = list(role_members.keys())
        for i in range(len(role_names)):
            for j in range(i + 1, len(role_names)):
                r1, r2 = role_names[i], role_names[j]
                overlap = len(role_members[r1] & role_members[r2])
                overlap_results[(r1, r2)] = overlap
        for (r1, r2), count in overlap_results.items():
            min_count = min(len(role_members[r1]), len(role_members[r2]))
            percent = (count / min_count * 100) if min_count > 0 else 0
            percent_r1 = (count / len(role_members[r1]) * 100) if len(role_members[r1]) > 0 else 0
            percent_r2 = (count / len(role_members[r2]) * 100) if len(role_members[r2]) > 0 else 0
            log.info(f"Overlap between '{r1}' and '{r2}': {count} members ({percent:.2f}%)"
                     + f"  - {percent_r1:.2f}% of '{r1}' also in '{r2}'"
                     + f"  - {percent_r2:.2f}% of '{r2}' also in '{r1}'")
        log.info(self.__class__.__name__ + " is ready")


def setup(bot:Bot):
    bot.add_cog(FormatOverlapCheck(bot))
